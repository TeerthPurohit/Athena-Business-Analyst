# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`kaynetics-agentic-python` is the **Python side** of the Kaynetics AI Agent Platform. A separate
Node.js repo (not in this checkout) owns auth, the API gateway, and org/billing data (Prisma). This
repo owns every prompt, every agent's configuration, and all LLM execution. See memory
`node-python-architecture-split`: the Prisma `Agent` table in the Node repo is unrelated per-org
custom agents, not this repo's domain agents.

**Prompts are configuration, not code.** They live in the `agent_prompts` DB table, seeded from
`prompt_seeds/*.py` via `prompt_seeds/seed.py` — never inline a prompt fallback in agent code (see
memory `prompts-live-in-database`).

## Architecture: two stacks under `agents/`

- **Stack A** — `agents/{business,campaign,orchestrator,main_brain,business_analyst,content,...}/`:
  simpler, single-purpose agent folders. `agents/orchestrator/nodes.py` (`SocialMediaOrchestrator`)
  is the LangGraph-style orchestrator that calls into `agents/business/`, `agents/content/`,
  `agents/creative/`, etc. `agents/shared/` holds cross-cutting services used by Stack A
  (`llm_service.py`, `credential_service.py`, `business_context_service.py`,
  `knowledge_search.py`, `jwt_context.py`).
- **Stack B** — `agents/universal-agent/`: its own deployable service (own `Dockerfile`,
  `docker-compose.yml`, `requirements.txt`, `alembic/`) built around a `BaseDomainAgent` +
  `DomainSpec` framework. Each sub-agent (`agents_scrapper/sub_agents/*.py` — `lead_generation.py`,
  `market.py`, `research.py`, `recruitment.py`, `social_trends.py`, etc.) is a `DomainSpec`
  registered in `AGENT_REGISTRY` and exposed at `POST /chat/{slug}` (`api/subagent_routes.py`).
  Stack B provides scraping/search primitives (wave scheduling, step-tree streaming,
  provenance/corroboration counting, field enrichment) that Stack A does not have.

A Stack A folder may import Stack B code (e.g. reuse a scraping primitive); the reverse is
backwards. `agents/business_analyst/` is a fourth pattern — lives in its own folder like Stack A,
but deliberately does **not** get Stack B's primitives for free; see
`agents/business_analyst/CLAUDE.md` for its own non-negotiables (append-only fact store, no
`eval()` in its Constraint Engine, dedicated `ba_embeddings` table, deterministic-planner-never-
calls-an-LLM rule) — those rules are scoped to that folder, not the whole repo.

`social_trends.py` is internal-only (called by other agents, not user-facing) — never edit it, even
to match a fix pattern used elsewhere.

## Services (docker-compose.yml)

`api`, `worker-scrape`, `worker-llm`, `worker-actions` (Prefect-orchestrated Celery-style workers),
plus `redis`, `flaresolverr` (anti-bot), `prefect-server`. The root `app/` package
(`app/main.py`, `app/api.py`, `app/brain_routes.py`, `app/ws_post_scheduler.py`) is the main
FastAPI app; `agents/universal-agent/api/` is Stack B's own separate FastAPI app.

## Commands

Run from repo root unless noted.

```bash
# Tests (root-level suite: app/, tests/, agents/*/test_*.py except universal-agent)
pytest
pytest path/to/test_file.py::test_name   # single test

# Tests (universal-agent has its own pytest config — run from inside that dir)
cd agents/universal-agent && pytest
cd agents/universal-agent && pytest path/to/test_file.py::test_name
```

`conftest.py` at root inserts `agents/universal-agent` onto `sys.path` so its `orchestration`
subpackage (a hyphenated dir name, not a valid package) can be imported by root-level tests that
touch the scraper orchestrator.

Schema changes: most tables are created via `Base.metadata.create_all()` in `models/engine.py`
(hand-written `migrations/00N_*.py` scripts for changes to existing tables). `agents/universal-agent`
and `agents/business_analyst` each have their **own** Alembic setup, scoped to their own tables only
— do not point root migrations at their models or vice versa.

## Claude Code config in this repo

- **Slash commands** (`.claude/commands/*.md`): `/agent`, `/architecture`, `/debug`,
  `/documentation`, `/feature`, `/migration`, `/optimization`, `/production`, `/refactor`,
  `/review`, `/security`, `/testing`, `/websocket` — each a scoped workflow (design/prompt
  engineering, architecture design, root-cause debugging, feature implementation, DB/API/service
  migration, perf/cost optimization, production readiness, safe refactoring, comprehensive code
  review, security review/threat modeling, test strategy, and scaffolding a new WebSocket endpoint,
  respectively).
- **Hook** (`.claude/settings.json` → `.claude/hooks/api-doc-reminder.sh`): fires on every
  `Write`/`Edit` of a `.py` file; if that file declares a FastAPI REST or websocket endpoint
  (`@*.get/post/put/delete/patch/websocket(`), it reminds Claude to update `docs/api.md` — one
  entry per endpoint (method + path or WS event name, purpose, request/response shape).
- **`.agents/`** is a separate cross-tool agent-definition kit (ag-kit/antigravity — its own
  `manifest.json`, `hooks.json`, `rules/`, and role-specific agent prompts under `.agents/agent/`)
  used to keep agent definitions in sync across multiple AI coding tools, not Claude Code's native
  subagent format. `.claude/` has no project-defined subagents (`.claude/agents/` does not exist);
  only Claude Code's built-in agent types are available here.

## Design docs

Non-trivial features get a design doc under `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
before implementation (see any existing file there for the expected shape: Scope, current-state
grounding with file:line references, explicit "Explicitly not done here" section).

## Engineering philosophy

- **Minimal and specific by default, not generic-by-default.** Build the narrow thing the task
  needs; generalize only once a second real caller needs the same logic (rule of three), not
  speculatively "for future products." This is a deliberate choice for this repo — resolved in
  favor of YAGNI over always-generic architecture.
- **When fixing shared logic, fix every caller**, not just the one the ticket names — grep first.
  This is about not leaving siblings broken, not about pre-building abstractions nobody asked for.
- **Never reinvent something the repo, stdlib, or an already-installed dependency already does.**
  If you pick a heavier option over a lighter one that was available, say why.
- **When unsure of an endpoint, agent name, DB schema, or existing behavior — read the source,
  don't guess.** This platform has many similarly-shaped agents (`lead_generation`, `market`,
  `research`, `recruitment`, `social_trends`, `stock_market`, `business_analyst`, `main_brain`,
  `content`/`creative`/`image_query`); details that look interchangeable often aren't.

## AI usage discipline

This platform's cost and latency are dominated by LLM calls, so:

- If deterministic code can solve it, don't reach for an LLM call.
- When an LLM call is genuinely needed, keep its context and tool-call count minimal, and prefer
  the smallest model that reliably does the job.
- Prompts are versioned in the DB (`agent_prompts`, seeded via `prompt_seeds/`) precisely so
  prompt changes are tracked like code changes — treat editing a live prompt with the same care as
  editing a migration.

## External API fallback pattern (already in use — follow it, don't reinvent it)

`agents/universal-agent/config/settings.py` already encodes the convention for adding any paid/
rate-limited external API: declare it as `Optional[str] = None` on `Settings`, unset key = that
layer is skipped entirely, and order fallbacks cheapest/free-tier first with paid options
explicitly commented as `LAST-RESORT` (see `RAYOBYTE_API_TOKEN` → `SCRAPEDO_API_TOKEN` →
`BRIGHTDATA_API_KEY` → `APIFY_API_TOKEN` in that file). A new external dependency should follow
this same shape — gated on a key, budget-capped if its free tier is a one-time pool rather than a
recurring quota, and never the first thing tried when a cheaper/free path could work.
