# BA: business context records, concurrent streamed ingestion, streaming analyst, tracing

Date: 2026-09-25. Scope: `agents/business_analyst/`, `prompt_seeds/ba_prompts.py`, `frontend/src/`.

## Why

An audit of the BA agent (six parallel reviews) found that the 35-section Business Context Object
existed but was used incorrectly, chat turns took ~25 s, uploads were analyzed one file and one
chunk at a time with no live progress, and no model thinking was streamed.

## Current state this changes (grounding)

- `business_context.py` stored every section as one flat dict, so a project could hold one
  stakeholder / process / risk; a list from the model failed `ProjectIR` validation and fell back
  to a fabricated IR (`semantic_planner.parse_user_request_to_ir`).
- `project_summary.py` asked the LLM to re-emit all 362 context fields every turn from every
  fact, although `extraction.py` had already validated and stored each field as a fact.
- Measured per chat turn: planner 11.5 s (output size), 3 sequential judge batches ~4.3 s,
  summary 4 s, ~1.9 s per uncached prompt fetch.
- `routes.upload_source_endpoint` analyzed chunks sequentially; chunks were 20,000 chars while
  the judge (`jev_client`) read only the first 12,000, silently rejecting tail findings.
- `project_harness` answered in one block via `ChatOpenAI.ainvoke`, which also drops
  OpenRouter's `reasoning` field, so thinking could not be streamed.

## Design

1. **Context records.** `ENTITY_KEY_FIELDS` marks 13 entity sections (stakeholders, processes,
   risks, decisions, …) as lists of records keyed by their identifying field. Each accepted
   record is one fact (`subject_key = "<section>:<normalized key>"`).
   `fold_business_context(facts)` builds the context deterministically (scalars: latest wins;
   lists: union; records: merged by key). The summary LLM now writes only the prose fields.
2. **Analyze / persist split.** `extraction.analyze_*` run the LLM + judge with no DB access;
   `persist_*` write through one session. Judge batches run with `asyncio.gather`.
3. **Concurrent streamed ingestion.** `POST /sources/stream` (multipart `files`, ≤20) runs every
   part of every file under `BA_INGEST_CONCURRENCY` (default 4). Each part is persisted as soon
   as it finishes, behind an `asyncio.Lock` on the one session (the pool is 3+2 connections, too
   small for a session per part), and emits a `tool_result` describing what it found. Chunks are
   capped at `JEV_EVIDENCE_CHARS` (12,000). Identical re-uploads are detected by content hash.
4. **Streaming analyst on LangGraph.** A `StateGraph` (`model → tools → model … → finish`) is
   shared by the lead analyst and its specialists. Completions use the raw OpenAI SDK
   (`stream=True`), relaying `thinking_delta` (reasoning) and `answer_delta` events.
5. **SSE hardening.** `_sse_response` runs work on its own session (a disconnect can't commit a
   half turn), sends keepalives every 15 s, and sets `X-Accel-Buffering: no`.
6. **Models.** Two tiers (product decision), each the other's fallback: chat tier
   `openai/gpt-6-luna` with reasoning off (chat-turn extraction, summaries, question phrasing;
   ~11 s on the planner prompt) and deep-reasoning tier `xiaomi/mimo-v2.6-pro` with reasoning
   on (project investigations: lead analyst and specialists). The generic `LLM_MODEL` is
   ignored: it named a DeepSeek-direct id that OpenRouter rejected on every call.
7. **Tracing.** Langfuse SDK 4.15, active only when keys are set. It records one trace per chat
   turn, investigation or upload, with `session_id` set to the project. Every model call is an
   explicit `generation` (model, usage, OpenRouter cost, reasoning). Agents, tools, retrievers
   and evaluators are typed. Emails and phone numbers are masked.
8. **Cost log.** `cost_log.py` records every model call of a run (a root observation) with its
   part, step, model, tokens, OpenRouter-billed cost, start offset and duration, and appends one
   Markdown section to `log.md` when the run finishes, with per-part subtotals and the run total.
   It does not depend on Langfuse.
9. **Prompts** are new seeded keys: `ba_semantic_planner_v2`, `ba_project_summary_v3`,
   `ba_lead_analyst_v1`, `ba_specialist_v1`, `ba_brd_narrative_v2`, `ba_options_narrative_v2`.

## Explicitly not done here

- Ingestion from CRM, Slack, email, web research, or previous projects; PRD, SOP, UAT, and
  business-case deliverables; clarification questions that target empty context fields.
- `user_id` on traces: `BATenantContext` carries no user identity.
- Per-part completion markers. A retry of a partially analyzed source re-records its parts that
  had succeeded.
- A test database. DB-backed tests now skip unless `BA_TEST_DB_CONFIRMED=1`.
