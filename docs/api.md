# API Reference (baseline snapshot)

This is a generated baseline snapshot of every REST and WebSocket endpoint in the FastAPI
backend, as of the commit this file was added. It is **not** auto-regenerated — a
PostToolUse hook (`.claude/hooks/api-doc-reminder.sh`) reminds Claude to update this file
by hand whenever a file containing FastAPI route/websocket decorators is edited. If an
endpoint below looks stale, check the source file referenced in each section header before
trusting it.

Router prefixes are combined with each route's path to give the full path. Endpoints
mounted directly on `app` (in `app/main.py`) have no router prefix.

---

## Business Agent — `/api/business`
Source: `app/api.py` (`router`)

- `POST /api/business/analyze` — Analyzes uploaded business documents (brand profile, tone, vision, USP, target audience, personas). Returns `CompiledBusinessContext`.

## Campaign Agent — `/api/campaign`
Source: `app/api.py` (`campaign_router`)

- `POST /api/campaign/generate` — Generates a multi-channel campaign strategy (monthly/weekly focus, channel allocations, milestones, budget). Body: `CampaignGenerationRequest` -> returns `CampaignStrategySchema`.

## Social Media Orchestrator — `/api/orchestrator`
Source: `app/api.py` (`orchestrator_router`)

- `POST /api/orchestrator/run` — Runs the Social Media orchestrator (O2) for a session; executes required sub-agents and returns final result. Body: `OrchestratorRequest` -> returns `OrchestratorResponse`.
- `GET /api/orchestrator/batch/current` — Status of the current/latest task batch for the logged-in user. Returns `Optional[TaskBatchStatus]`.
- `WS /api/orchestrator/stream` — One-shot per-connection streaming version of `/run`. Client sends `{user_query, session_id, company_id, organization_id, agent_id, user_id, task_id, clarifying_answers}`. Server sends `StreamChunk` messages typed `status`, `token`, `metrics`, per-agent result types (`image_result`, `campaign_section`, `post_scheduled`, `business_profile_result`, `trend_result`, `content_result`, `creative_result`), `error`, and a final `done`.

## Admin Prompt Version Control — `/api/admin/prompts`
Source: `app/api.py` (`admin_prompt_router`)

- `GET /api/admin/prompts/agents` — Distinct list of `agent_id`/`agent_name`/`prompt_key` combos. Returns `List[AgentCatalogResponse]`.
- `GET /api/admin/prompts/list` — List all prompt versions, optional `agent_id`/`prompt_key` filters. Returns `List[PromptVersionResponse]`.
- `POST /api/admin/prompts/new` — Create a new versioned prompt (auto-incremented version). Body: `CreatePromptVersionRequest` -> `PromptVersionResponse`.
- `POST /api/admin/prompts/activate` — Activate a specific prompt version, deactivating others. Body: `ActivatePromptVersionRequest` -> `PromptVersionResponse`.
- `DELETE /api/admin/prompts/delete` — Delete a prompt version (query params `agent_id`, `prompt_key`, `version`). v1 and the active version are protected.

## Super Admin Configuration — `/api/admin`
Source: `app/api_admin.py` (`admin_router`)

- `GET /api/admin/settings` — Current active system configuration merged with `.env` defaults. Returns `AdminSettingsResponse`.
- `PUT /api/admin/settings` — Update project parameters. Body: `AdminSettingsUpdateRequest` -> `AdminSettingsResponse`.
- `DELETE /api/admin/settings/{config_id}` — Delete a custom configuration, rolling back to defaults. Returns `MessageResponse`.
- `GET /api/admin/main-agents` — List all enabled main orchestrator agents. Returns `List[MainAgentResponse]`.
- `POST /api/admin/providers` — Create/update a provider credential (llm/scraping/image). Body: `ProviderCredentialCreate` -> `ProviderCredentialResponse`. Super-admin gated.
- `GET /api/admin/providers` — List provider credentials, optional `category` filter (API keys masked). Super-admin gated.
- `PATCH /api/admin/providers/{credential_id}` — Update a provider credential. Body: `ProviderCredentialUpdate` -> `ProviderCredentialResponse`. Super-admin gated.
- `DELETE /api/admin/providers/{credential_id}` — Delete a provider credential. Super-admin gated.
- `POST /api/admin/providers/{credential_id}/sync` — Sync available models for a provider credential. Returns `ModelSyncResponse`. Super-admin gated.
- `GET /api/admin/models` — List model catalog entries, optional `category` filter, with per-agent classification. Returns `List[ModelCatalogResponse]`. Super-admin gated.
- `POST /api/admin/models` — Register a new model catalog entry. Body: `ModelCatalogCreate` -> `ModelCatalogResponse`. Super-admin gated.
- `PATCH /api/admin/models/{model_id}` — Update model enable/default/display/quality and per-agent allow-lists. Body: `ModelCatalogUpdate` -> `ModelCatalogResponse`. Super-admin gated.
- `DELETE /api/admin/models/{model_id}` — Delete a model catalog entry. Super-admin gated.
- `GET /api/admin/plans` — List subscription plans. Returns `List[PlanResponse]`. Super-admin gated.
- `GET /api/admin/plans/{plan_id}/models` — List models entitled to a plan. Returns `List[ModelCatalogResponse]`. Super-admin gated.
- `POST /api/admin/plans/{plan_id}/models` — Attach a model to a plan (idempotent). Body: `PlanModelAttachRequest`. Super-admin gated.
- `DELETE /api/admin/plans/{plan_id}/models/{model_id}` — Detach a model from a plan. Super-admin gated.

Note: `require_super_admin` is dormant until `SUPER_ADMIN_ENFORCED=true`; currently these endpoints resolve the caller but don't block on role.

## Main Brain Agent — `/api/brain`
Source: `app/brain_routes.py` (`brain_router`)

- `GET /api/brain/models` — List models available for the current company, category `llm` or `image` (query param). Returns `List[ModelOption]`.
- `GET /api/brain/model-preference` — Current model preference (admin-set default, not per-user) for a category. Returns `ModelPreferenceResponse`.
- `PUT /api/brain/model-preference` — **Disabled** — always returns HTTP 410 (per-user model selection has been turned off; model preference is admin-set only).
- `GET /api/brain/exports/{session_id}/{fmt}` — Download a CSV/XLSX export generated during a run (`fmt` in `csv`/`xlsx`).
- `GET /api/brain/replay/{session_id}` — Replay every step/status/metrics/done chunk persisted for a finished run (`token` chunks excluded).
- `POST /api/brain/run` — Unified non-streaming meta-orchestrator entry point; classifies scope and delegates to scraper/social orchestrators. Body: `BrainAgentRequest` -> `BrainAgentResponse`.
- `WS /api/brain/stream` — Streaming version of `/run`. Client sends `{user_query, session_id, ...}`. Server sends `StreamChunk`s typed `status`, `token`, `metrics`, per-agent result types, `error`, final `done` (with `in_scope`, `target_orchestrators`, `artifacts`, `tokens_used`, `agents_called`, `confidence`).

## Post Scheduler — `/api/scheduler`
Source: `app/api_post_scheduler.py` (`router`)

- `POST /api/scheduler/generate/festivals` — Batch-generate posts for all festivals in a date range. Body: `GenerateFestivalPostsRequest` -> `GenerateResponse`.
- `POST /api/scheduler/generate/custom` — Generate a post for a specific event/topic. Body: `GenerateCustomEventRequest` -> `ScheduledPostResponse`.
- `POST /api/scheduler/generate/campaign` (202) — Trigger background generation of all daily posts for a campaign.
- `POST /api/scheduler/posts/{post_id}/review` — Approve or reject a post in one call. Body: `ReviewPostRequest` -> `ScheduledPostResponse`.
- `POST /api/scheduler/posts/{post_id}/approve` — Convenience wrapper around `/review` with `action=approve`.
- `POST /api/scheduler/posts/{post_id}/reject` — Convenience wrapper around `/review` with `action=reject`. Body: `RejectPostRequestPayload`.
- `GET /api/scheduler/posts` — List posts for a company, filters: `status`, `platform`, `month`, `year`. Returns `list[ScheduledPostResponse]`.
- `GET /api/scheduler/posts/{post_id}` — Get a single post with feedback history. Returns `ScheduledPostResponse`.
- `GET /api/scheduler/calendar/{year}/{month}` — Calendar view of posts grouped by day. Returns `CalendarMonthResponse`.
- `DELETE /api/scheduler/posts/{post_id}` — Delete a scheduled post. Returns `MessageResponse`.

## Post Scheduler WebSocket
Source: `app/ws_post_scheduler.py` (`router`, no prefix)

- `WS /ws/scheduler/{company_id}` — Real-time post update notifications. Requires JWT via `?token=` or `Authorization` header. Client can send `"ping"` -> server replies `{"type": "pong"}`; other pushed messages come from `agents.Post_Scheduling.websocket_manager.manager`.

## Universal Scraping Sub-Agents
Source: `agents/universal-agent/api/subagent_routes.py` (`subagent_router`, no prefix — mounted at root)

- `GET /subagents/catalog` — Catalog of chatable agents (Brain, domain sub-agents, Image Query, Social Media Orchestrator) with model preference/allowed-models info. Auth required.
- `GET /chat/replay/{session_id}` — Replay persisted stream chunks for a finished sub-agent run.
- `POST /chat/{slug}` — One route per entry in `AGENT_REGISTRY` (slugs: `stock-market`, `research`, `market`, `recruitment`, `lead-generation`, plus any other registered domain agent except `social_trends`, which is internal-only). Body: `ChatRequest` -> `ChatResponse`.
- `WS /chat/{slug}/stream` — Streaming counterpart for every registered agent except `social_trends`. Protocol: client sends `{"message", "session_id"}` (or `?session_id=` query param); server sends `StreamChunk`s (`status`, `token`, final `done`), `error` on failure.

Note: `/api/trends/search` (chat_endpoint), `/api/image-query/generate` and `/api/orchestrator/run` are referenced in this file's `_STANDALONE_CHAT_AGENTS` catalog metadata but are actually defined in `app/main.py` / `app/api.py` (see below) — they are not routes on this router.

## Universal Scraper Orchestrator
Source: `agents/universal-agent/api/orchestrator_routes.py` (`scraper_orchestrator_router`, no prefix)

- `WS /chat/orchestrator/stream` — Routes a free-form message to the best scraper sub-agent, streaming routing decision + agent output. Client sends `{"message", "session_id"}`. Server sends `StreamChunk`s (`status`, `token`, `done`, `error`).
- `GET /chat/orchestrator/replay/{session_id}` — Replay persisted stream chunks for a finished orchestrator run. Auth required.
- `POST /chat/orchestrator` — Non-streaming fallback: classify + run single best scraper sub-agent. Body: `ChatRequest` -> `ChatResponse`.

## Universal Scraper Agent — core routes
Source: `agents/universal-agent/api/routes.py` (`router`, no prefix — mounted at root as `universal_router`)

- `POST /scraper/jobs` (202) — Submit a background scrape job (Celery). Body: `ScrapeJobCreateRequest` -> `ScrapeJobCreateResponse`.
- `GET /scraper/jobs/{job_id}` — Scrape job status; syncs Celery task completion into Postgres. Returns `ScrapeJobStatusResponse`.
- `GET /scraper/jobs/{job_id}/results` — Paginated results for a completed job (`limit`/`offset`; `format=html` renders an HTML report). Returns `ScrapeJobResultsResponse`.
- `WS /stream` — General chat pipeline streaming endpoint. Client sends `{"message", "session_id"}` (JWT optional via `?token=`). Server sends `StreamChunk`s (`status`, `token`, `done`, `error`).
- `GET /chat/sessions` — All session IDs + metadata for the authenticated user (JWT `sub`). Returns `UserSessionIdsResponse`.
- `GET /chat/history` — All conversation-history sessions for the authenticated user. Returns `UserChatHistoryResponse`.
- `GET /chat/history/{session_id}` — Full conversation history for one session. Returns `SingleSessionHistoryResponse`.
- `DELETE /chat/history/{session_id}` — Delete a chat session's conversation history. Returns `DeleteSessionResponse`.
- `GET /sessions/{session_id}/results` — All stored results + chat history for a session (`format=html` renders an HTML report). Returns `ResultsResponse`.
- `GET /sessions/{session_id}/export.csv` — Download the CSV export generated for a session.
- `GET /sessions/{session_id}/export.xlsx` — Download the Excel export generated for a session.
- `GET /sessions/{session_id}/history` — Chronological chat history for a session, no auth required. Returns `HistoryResponse`.
- `GET /analytics` — Aggregated per-turn usage analytics (intent mix, token cost, latency) from DuckDB.
- `GET /health` — Infrastructure health check (Redis, Postgres async/sync, LLM, Celery workers, FlareSolverr). 200 if core services (Redis+Postgres) healthy, 503 if degraded. Returns `HealthResponse`.
- `POST /chat/new` (201) — Create a new chat session (or reuse an existing empty untitled one). Returns `ChatSessionCreateResponse`. Auth required.
- `POST /chat` — Run a full pipeline chat turn on an existing session. Body: `ChatRequest` -> `ChatResponse`.
- `DELETE /sessions/{session_id}` — Delete a session, its messages, and its scraped results. Returns `DeleteSessionResponse`.
- `POST /sessions/{session_id}/stop` — Stop active scraper/orchestrator/brain tasks for a session. Returns `StopSessionResponse`.
- Mounts `actions/emailer/tracker.py`'s `router` (prefix `/webhooks/email`, tag `email_tracker`) as a sub-router — see that file for its own routes (not enumerated here, out of scope for this pass).

## Root App Endpoints (mounted directly on `app`)
Source: `app/main.py`

- `GET /` — Service info banner (excluded from OpenAPI schema).
- `GET /health` — Simple `{"status": "healthy"}` liveness check (distinct from the richer `/health` on the universal router above — same path, different router; FastAPI resolves by registration order).
- `POST /api/trends/search` — Discovers active market trends/search volume/industry insights for a topic. Body: `TrendRequest` -> `AgentResponse`.
- `WS /ws/trends/search` — Live version of the above; streams a status update per LangGraph node plus the final report token-by-token. Drives the graph directly in-process (no Redis relay).
- `POST /api/generate` — Generates marketing copywriting assets/captions/social posts grounded in company context and trend insights. Body: `ContentGenerationRequest`.
- `POST /api/generate/simple` — Simplified content generation; resolves user/company via `session_id` and extracts missing parameters via structured LLM prediction (see `app/api_content.py`'s `ContentExtractionResult`/`extract_content_parameters`). Body: `SimpleContentGenerationRequest`.
- `WS /api/generate/simple/stream` — Live version of `/api/generate/simple`; relays `trend_result`/`content_result` chunks as produced instead of one blob.
- `POST /api/creative/generate` — Triggers the Creative Intelligence Agent to generate visual content concepts from a free-form request. Body: `CreativeGenerationRequest` -> `CreativeGenerationResponse`.
- `POST /api/content/generate-image` — Generate an image from a text prompt + platform. Body: `ImageGenerationRequest` -> `GeneratedImageResult`.
- `POST /api/image-query/generate` — Standalone image generation from a free-form NL query, tracked by `session_id`. Body: `ImageQueryRequest` -> `ImageQueryResponse`.
- `WS /api/image-query/generate/stream` — Live version of the above; relays `image_result` chunks (generating -> ready/failed).
- `POST /extraction/extract` — Extract structured data (products, brand info, personas, FAQs) from an uploaded business document via LLM, optionally auto-populating the Node backend. Body: `ExtractionRequest`.
- `GET /files/{bucket}/{path}` — Proxies/serves files from MinIO storage (used so a dev tunnel URL can serve stored images).

`app/api_content.py` defines no routes of its own — it's a helper module (`SimpleContentGenerationRequest`, `ContentExtractionResult`, `extract_content_parameters`) consumed by `POST /api/generate/simple` above.

---

## Business Analyst OS — `/api/ba` + `/api/ba/projects`
Source: `agents/business_analyst/api/routes.py` (`ba_router`, `ba_projects_router`)

### Local account authentication (`/api/auth`)

- `POST /api/auth/register` — Create an account and isolated organization. Body: `{name, email, password}` (password minimum 12 characters). Returns `{access_token, token_type, user}` (201).
- `POST /api/auth/login` — Exchange `{email, password}` for a 12-hour HS256 JWT. Returns `{access_token, token_type, user}`.
- `GET /api/auth/me` — Return the current account for a Bearer JWT.

Set `JWT_ACCESS_SECRET` to a stable random value of at least 32 bytes. Passwords are stored as Argon2 hashes. BA routes use the token's `org_id` claim and still validate project ownership in the database.

### Project Lifecycle (`ba_projects_router` — no project_id, JWT org-only auth)

- `POST /api/ba/projects` — Creates a new BA project for the caller's org. Optionally applies an industry template (field values in body always override template defaults). Body: `ProjectCreateRequest {name, instructions?, must_have?, should_have?, industry_template_key?}` → returns `{id, org_id, name, settings, created_at, updated_at}` (201 Created).
- `GET /api/ba/projects` — Lists all BA projects for the caller's org, newest first. Returns `List[{id, name, settings, created_at, updated_at, summary_updated_at}]`.

### Project Settings + Summary (`ba_router` — requires `project_id` JWT)

- `PATCH /api/ba/projects/{project_id}` — Partial update of project name and/or settings sub-fields. Merges; unmentioned sub-fields are preserved. Supports direct user edits to `project_summary`. Body: `ProjectPatchRequest {name?, instructions?, must_have?, should_have?, project_summary?}` → returns `{id, name, settings, updated_at, summary_updated_at}`.
- `DELETE /api/ba/projects/{project_id}` — Permanently deletes an owned project, its BA and source-evidence records, local uploads, and project-scoped stored deliverables. Returns 204 with no body; missing or foreign projects return 404. If required object storage is unavailable, deletion is refused rather than leaving known artifacts behind.
- `POST /api/ba/projects/{project_id}/summary/regenerate` — Triggers AI regeneration of `project_summary` from current BaFact graph using prompt `ba_project_summary_v1`. On LLM failure, existing summary is left untouched (BA_PROJECT_SUMMARY_DEGRADED logged). No body → returns `{project_id, project_summary, summary_updated_at}`.

### Existing Routes (`ba_router`)

- `POST /api/ba/planner/semantic` — Parses a natural language user request into a `ProjectIR` object using prompt `ba_semantic_planner_v2` (`agent_id="9"`), including the canonical 35-section `business_context` (entity sections such as stakeholders, processes, and risks are lists of records). Body: `SemanticPlannerRequest` → returns `ProjectIR`; 503 when the model is unavailable (no fabricated fallback).
- `POST /api/ba/projects/{project_id}/sources` — Stores an uploaded source, analyzes its full extracted text in bounded chunks for requirements and wider project facts, and refreshes the structured scope. Supports text, Markdown, selected source code formats, Word, and text-bearing PDF files. Per-chunk failures are reported and recorded without inventing findings; scanned PDFs still require OCR. Chunks are at most 12,000 characters (the finding judge's evidence window) and are analyzed concurrently. Re-uploading identical content returns the existing source with `duplicate: true` instead of re-analyzing it. Max 25 MB. Returns source provenance, `extraction` counts, `extraction_issue`, `duplicate`, and `project_summary` (201 Created).
- `POST /api/ba/projects/{project_id}/sources/stream` — Multipart `files` (1–20 files, 25 MB each). Saves every file, analyzes all parts of all files concurrently (`BA_INGEST_CONCURRENCY`, default 4), records each part as it finishes, and streams Server-Sent Events: `status`, `tool_call` (`tool: analyze_source`, `file`, `part`, `parts`, `message: "Reading <file> (part n of m)"`), `tool_result` (same fields, `message` describes what was found, e.g. requirements and stakeholders), `tool_call`/`tool_result` for `synthesize_project_scope`, then `result {sources: [per-file entry as above, or {ref, error}], project_summary}`, `done` — or `error {message}`. `: keepalive` comments are sent every 15 s.
- `GET /api/ba/projects/{project_id}/sources` — Lists registered source documents for the project, newest first.
- `GET /api/ba/projects/{project_id}/requirement-package` — Pure, tenant-scoped projection of cited requirements, categories, deterministic user stories, Gherkin scenarios, ambiguity agenda items, and traceability rows. Makes no LLM call and writes no facts.
- `POST /api/ba/projects/{project_id}/chat` — Extracts structured project facts from a free-text chat message via the semantic planner and persists them to the append-only fact store against a lazily-created `kind="chat"` source. Regenerates the project summary only when something new was recorded. Body: `ChatMessageRequest {message}` → returns `{facts_created, goal_facts, entity_facts, reply, ir, project_summary, summary_updated_at}` where `reply` is a plain-language acknowledgment built from what was recorded; 503 when validation or the model is unavailable (nothing recorded).
- `POST /api/ba/projects/{project_id}/sources/{source_id}/analyze` — Retries analysis of an already-uploaded source through the same chunked, concurrent pipeline as upload, with its original provenance. A source whose upload analysis fully completed is not re-analyzed (`{facts_created: 0, already_analyzed: true, existing_fact_count}`). Otherwise returns `{facts_created, requirements_created, gaps_created, extraction_issue, already_analyzed: false, project_summary, summary_updated_at}`.
- `GET /api/ba/projects/{project_id}/facts` — Returns current active facts for a project by walking replacement chains. Returns `List[Dict]`.
- `POST /api/ba/projects/{project_id}/facts/{fact_id}/approve` — Distinct approval verb; sets `human_approval=True` on a new fact copy. Body: `FactApproveRequest` → returns `{status, approved_fact_id, subject_key}`; 404 if the fact is not in the project or was already superseded.
- `GET /api/ba/projects/{project_id}/deliverables` — Lists deliverable instances with lazily-computed staleness. Returns `List[Dict]`.
- `POST /api/ba/projects/{project_id}/deliverables/{key}/generate` — Generates a deliverable instance using the catalog renderer. Returns `{id, deliverable_key, status, frontier_seq, content}`.
- `POST /api/ba/projects/{project_id}/deliverables/{instance_id}/approve` — Distinct approval verb; sets `status='approved'` on a deliverable instance. Body: `DeliverableApproveRequest` → returns `{status, instance_id, approved_by, approved_at}`.
- `POST /api/ba/projects/{project_id}/runs` — Starts a new BA OS execution run. Body: `RunStartRequest` → returns `{run_id, status}`.
- `GET /api/ba/projects/{project_id}/runs/{run_id}` — Gets execution status of a run → returns `{run_id, status, planner_mode}`.

### Project investigation (`ba_router`)

- `POST /api/ba/projects/{project_id}/chat/intent` — Uses Jev to classify a free-form message as a clarification answer, project context update, evidence investigation, or deliverable request. Deterministic fallbacks apply when Jev is unavailable or uncertain.
- `POST /api/ba/projects/{project_id}/chat/stream` — Server-Sent Events: `tool_call`/`tool_result` for `extract_project_facts` (result message lists what was confirmed) and, only when something new was recorded, `synthesize_project_scope`; then `result {facts_created, reply, project_summary}` and `done`, or `error {message}`. Runs on its own DB session, so a client disconnect never commits a half-recorded turn. `: keepalive` comments every 15 s.
- `POST /api/ba/projects/{project_id}/analyze` — Investigates the active project's recorded facts and uploaded documents and records the question and answer in the project conversation. Body: `{ "question": "..." }`. The lead analyst can use project tools or delegate to restricted evidence and scope specialist agents; response: `{ "answer": "...", "tools_used": [...] }`. All lookups enforce the authenticated organization and project. Requires configured LLM credentials. With `OPENROUTER_API_KEY`, search combines cached `google/gemini-embedding-001` vectors (3,072 dimensions by default) with keyword matching and reports `retrieval_mode` in the search tool result; it falls back to keyword search when embeddings are unavailable.
- `POST /api/ba/projects/{project_id}/analyze/stream` — Server-Sent Events for the same investigation (a LangGraph tool loop: lead analyst plus evidence/scope specialists): `status`, `tool_call {tool, specialist}`, `tool_result {tool, message}`, `thinking_delta {specialist, text}` (the model's reasoning as it streams), `answer_delta {text}` (answer tokens; text streamed before a tool call is a preamble and is superseded), `answer {text}` (final, authoritative), `done` — or `error {message}`. `: keepalive` comments every 15 s.

### Smart Clarification Engine (`ba_router`)

- `GET /api/ba/projects/{project_id}/clarifications/next` — Returns the next unresolved clarifying question. Ranks graph gaps deterministically, skips already-asked-unresolved ones, uses Jev through OpenRouter to choose among up to three eligible gaps when confident (otherwise retains deterministic order), phrases the chosen question via LLM (`ba_clarification_question_v1`), records it as a BaFact. Returns `{gap_key, node_id, field, node_type, question, score}` or `{question: null, gap_key: null, message: "All open questions are answered."}`.
- `POST /api/ba/projects/{project_id}/clarifications/{gap_key}/answer` — Records the stakeholder's answer. Runs deterministic conflict detection against prior answers for the same gap (kNN-gated, then value-inequality check). Body: `ClarificationAnswerRequest {answer}` → returns `{answer, conflict_notice}` where `conflict_notice` is a phrased notice string or `null`.
- `WS /api/ba/projects/{project_id}/chat/stream` — Streams real chat-driven fact extraction (same auth pattern as the clarify stream: `?token=`). Client sends `{"message": "..."}`; server extracts via the semantic planner and persists real `BaFact` rows (no mock reply), streams the same plain-language `reply` as the REST endpoint word-by-word (`{"type":"token",...}`), then sends `{"type":"done","facts_created","goal_facts","entity_facts","ir","project_summary","summary_updated_at"}`. Errors are sent as generic `{"type":"error","detail"}` messages (details are logged server-side, never sent to the client). Loops on the same socket for further messages.
- `WS /api/ba/projects/{project_id}/clarifications/stream` — Streaming version of the clarify loop above, on its own router (`ba_ws_router`, mounted separately from `ba_router` in `app/main.py` since browser WS handshakes can't carry the router's header-based JWT dependency). Auth via `?token=` query param (`get_ba_tenant_context_from_token`, same JWT/exp/org_id/BaProject-lookup rules as the REST routes). Server sends `{"type":"status","stage":"thinking"|"recording"}`, `{"type":"token","text":...}` (question/conflict text streamed word-by-word — the underlying LLM call is a single structured-output completion, not a native token stream), `{"type":"question", gap_key, node_type, field, score}`, `{"type":"conflict","text":...}` + `{"type":"conflict_done"}`, `{"type":"done","message":...}` (closes), or `{"type":"error","detail":...}`. Client sends `{"answer": "..."}`; server then auto-advances to the next question.
