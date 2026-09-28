# agents/business_analyst/ — folder guide

This folder is both the **design/spec home** and the **code home** for the Business Analyst OS
(BA OS).

- [specification.md](specification.md) — the spec as handed off (v1.0, draft).
- [architecture_plan.md](architecture_plan.md) — the reviewed design: six subagents each audited
  one subsystem against the real codebase; three corrected claims in the original draft. **Where
  the two docs disagree, architecture_plan.md wins** — it has the reasoning, not just the
  conclusion, **except §1 and §8's "BA goes in Stack B"**: that call was overridden — BA lives
  here, as its own folder (a fourth, Stack-A-shaped pattern alongside
  `agents/{business,campaign,orchestrator,main_brain}/`), not under
  `agents/universal-agent/agents_scrapper/`.

Living as its own folder means BA does not get Stack B's primitives (wave scheduling, Celery
durability, step-tree streaming, provenance/corroboration counting) for free — reuse them by
importing across from `agents/universal-agent/` where it makes sense; there is no rule against
a Stack A folder depending on Stack B code, only the reverse would be backwards.

## Non-negotiables (do not "simplify" these away)

- **No `eval()`** in the Constraint Engine — operators are a fixed whitelist (`equals`,
  `greater_than`, `less_than`, `contains`, `exists`).
- **Facts are append-only**, enforced by a DB trigger, not `REVOKE` (see architecture_plan.md
  §4.1 for why `REVOKE`/rewrite-rules were rejected).
- **Deterministic planner never calls an LLM.** Tests must assert zero LLM calls against a
  mocked client.
- **Dedicated `ba_embeddings` table**, `org_id NOT NULL`, tenant filter in the same statement as
  `ORDER BY`/`LIMIT`. Never the shared `embeddings` table (see the platform-wide S3 finding in
  architecture_plan.md §0).
- **JWT-only auth**, no header/query-param fallback, no default org — mirrors this repo's
  `BATenantContext` pattern, not the header-override bug already found elsewhere in the codebase.
- **`human_approval` / `source_tier` / `evidence_confidence`** are writable only through the
  approval endpoint and the deterministic scorer — never a body-field `PATCH`.
- **Prompts are configuration, not code** — see memory `prompts-live-in-database`: seed via
  `prompt_seeds/seed.py` into `agent_prompts`, never inline a prompt fallback in BA capability
  code.
- Derivation capabilities emit **structured fields**, not prose — this is what lets projections be
  LLM-free (architecture_plan.md §2.1). If a projection needs an LLM call, the bug is upstream in
  derivation.

## Before implementing here

Read architecture_plan.md §9 (Verification) and §11.7 — they're the pass/fail bar for anything
built against this spec, not just documentation.
