# BA OS — Chat & Document Fact Extraction

## Scope

Real (non-mock) backend support for turning free-text chat messages and uploaded-document
content into `BaFact` rows, replacing the frontend's simulated chat reply and fabricated
counters. Out of scope: the Knowledge Graph nodes/edges read-model, Knowledge Coverage %, Open
Gaps count, Recent Decisions, Known Constraints — these stay hidden/blank in the UI until a
separate future feature builds the graph read-model (per
`docs/superpowers/specs/2026-08-05-ba-os-product-spec/08-developer-notes.md`).

## Current-state grounding

- `build_project_ir_llm(text)` (`agents/business_analyst/semantic_planner.py`, used by
  `POST /api/ba/planner/semantic`) already turns free text into a structured `ProjectIR`
  (objectives/entities/goals/scope) via LLM — but only returns it, never persists facts, and its
  route endpoint has a project_id/auth mismatch (route has no `{project_id}` path segment while
  its dependency expects one) — not reused as-is.
- `assert_fact()` (`agents/business_analyst/facts.py:58`) is the only way to write a `BaFact` —
  requires `source_id` pointing at a real `BaSource`.
- `_get_or_create_system_source()` (`agents/business_analyst/clarification.py:64`) is the existing
  pattern for a lazily-created, per-project synthetic source (`kind="system"`) — reused here for a
  `kind="chat"` variant.
- `agents/business_analyst/api/routes.py`'s new `upload_source_endpoint` (today's earlier work)
  writes uploaded file bytes to local disk keyed by content_hash — document analysis reads that
  same file back.
- No PDF/DOCX text-extraction library exists anywhere in the repo (`requirements.txt` has neither
  `python-docx` nor `pypdf`).

## Design

**`agents/business_analyst/document_text.py`** (new): `extract_text(filename: str, content: bytes) -> str`.
Dispatches on extension: `.txt`/`.sql` → `content.decode("utf-8", errors="replace")`; `.docx` →
`python-docx`; `.pdf` → `pypdf`. Unsupported extensions raise `ValueError` (surfaced as 400).

**`agents/business_analyst/extraction.py`** (new): `extract_and_persist_facts(ctx, session, *,
text, source_id, asserted_by) -> dict`. Calls `build_project_ir_llm(text)`, then writes one
`BaFact` per IR field via `assert_fact` (mirrors `ProjectIR`'s own structure — matches the "one
fact per IR field" mapping already used for scope decisions elsewhere in this spec set):

| ProjectIR field | subject_type | subject_key | predicate |
|---|---|---|---|
| `objectives[]` | `goal` | `obj.id` | `objective` |
| `entities[]` | `entity` | `entity.name` | `described_as` |
| `goals[]` | `goal` | `goal.id` | `target_metrics` |
| `scope.in_scope` | `project` | `ctx.project_id` | `in_scope` |
| `scope.out_of_scope` | `project` | `ctx.project_id` | `out_of_scope` |
| `scope.constraints` | `project` | `ctx.project_id` | `constraint` |

Returns `{"facts_created": int, "goal_facts": int, "entity_facts": int, "ir": ProjectIR}` so
callers don't need a second round-trip to report counts.

**Two new endpoints** on `ba_router` (`agents/business_analyst/api/routes.py`):

- `POST /api/ba/projects/{project_id}/chat` — body `{message: str}`. Gets/creates the project's
  `kind="chat"` synthetic source (new sibling to `_get_or_create_system_source`), runs
  `extract_and_persist_facts`, commits, returns the counts dict.
- `POST /api/ba/projects/{project_id}/sources/{source_id}/analyze` — loads the named `BaSource`
  (tenant-checked), reads its file back from `UPLOAD_DIR` by `content_hash`/`ref`, extracts text
  via `document_text.extract_text`, runs `extract_and_persist_facts` with `source_id` = the real
  uploaded source (correct provenance, distinct from chat-derived facts), commits, returns the
  same counts shape.

**Frontend (`ba-frontend/app.js`)**: delete the `welcome_placeholder` mock-reply branch and
`generateProjectMockData` entirely. Chat submit calls `POST .../chat` for real, streams the
returned counts into the message list, then refreshes `Extracted Facts`/`Active Requirements` in
the right panel from a real `GET /facts` count (no separate rollup endpoint needed — the panel
counts client-side). `Project Summary` wires to the already-real
`POST .../summary/regenerate`. `Knowledge Coverage`, `Open Gaps`, `Recent Decisions`, `Known
Constraints` are hidden (not rendered) until the graph read-model exists — never fabricated.

## Explicitly not done here

- PDF/DOCX extraction quality (tables, images, multi-column layout) — plain text extraction only.
- Requirement-specific subject_type (`requirement` vs `goal`) — deferred; IR has no separate
  "requirement" concept today, only objectives/goals/entities.
- Re-running extraction idempotently (calling `/chat` twice with the same message creates
  duplicate facts — append-only store has no dedup layer here; acceptable for v1, matches how
  every other assert_fact call site in this codebase already behaves).
