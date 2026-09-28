# WebSocket streaming for Trend Intelligence search

## Context

`POST /api/trends/search` ([app/main.py:355-377](../../../app/main.py#L355)) runs
`TrendAgent.run()` ([agents/trend/trend_agent.py:227](../../../agents/trend/trend_agent.py#L227)),
which invokes an 8-node LangGraph pipeline (`agents/trend/trend_agent.py:46-70`) end to
end via `graph.ainvoke()` and returns one `AgentResponse` blob once everything finishes.
A full run can take a while (intent parsing → provider fetch → normalize → dedupe →
rank → LLM synthesis), during which the client sees nothing.

A draft plan proposed a `/ws/trends/search` endpoint that streams per-node status and
a final result, modeled on this codebase's existing chunk-based WebSocket streams
(`StreamChunk`, used by `/api/generate/simple/stream`, `/api/image-query/generate/stream`,
the orchestrator/subagent/brain chat streams). This spec adopts that goal but corrects
two assumptions in the draft plan after checking the actual code (see Decisions below),
and adds true token-level streaming for the final synthesis step, which the draft plan
did not include.

## Goal

Add `/ws/trends/search`, a WebSocket sibling to `/api/trends/search`, that streams:
1. A status update as each graph node starts.
2. The final report synthesis, token-by-token, as it's generated.
3. A final result frame with the complete `AgentResponse`.

The existing REST endpoint is unchanged and keeps working exactly as it does today.

## Decisions (deviating from the draft plan)

**1. Auth is soft-fail, matching every existing endpoint in this file — not strict.**
The draft plan said "verify JWT, close 1008 if invalid or missing." But
`get_current_company_id` ([app/api.py:67-100](../../../app/api.py#L67)), which the REST
trends endpoint depends on, never hard-fails on bad auth — it falls back to
`DEFAULT_COMPANY_ID` (env, default `"default_company"`). The two existing WebSocket
routes in `app/main.py` (`generate_content_simple_stream`, `generate_image_from_query_stream`)
do the same. Making trends the one strict-auth WS route in the file would be an
inconsistency with no stated reason, so this endpoint follows the established soft-fail
pattern: verify the token if present, don't close the socket over a missing/invalid one,
resolve `company_id` through the same fallback chain (`message payload → JWT claims →
DEFAULT_COMPANY_ID`).

**2. No Redis pub/sub relay — the graph is driven directly in the WebSocket handler.**
Every existing WS route in this file uses `publish_to_stream` (Redis, with an
in-memory fallback) plus `_relay_brain_stream`, which runs the work as a background
`asyncio.Task` and polls a pub/sub channel to relay chunks. That machinery exists to
support session replay and multi-worker fanout — a client reconnecting mid-run, or a
second tab picking up the same session. Trend search doesn't need that: it's a single
request/response over one connection, opened, used once, and closed. So this endpoint
skips `publish_to_stream`/`_relay_brain_stream` entirely and drives
`trend_agent.graph.astream_events(initial_state, version="v2")` directly inside the
WebSocket coroutine, writing each event straight to the socket. This is the endpoint's
own optimization: no Redis round-trip per status/token event, no polling interval,
lower latency per chunk, one fewer dependency for this specific path to fail on.
Verified compatible with the installed `langgraph==1.2.9` / `langchain-core==1.5.1`.

**3. Token streaming requires switching the synthesis call from `ainvoke` to `astream`.**
`trend_service.analyze_trends` ([agents/trend/trend_service.py:165-218](../../../agents/trend/trend_service.py#L165))
makes one JSON-mode `model.ainvoke(prompt_val)` call to produce the structured
`TrendReport`. `astream_events` only surfaces `on_chat_model_stream` events for calls
made via `.astream()`. This spec changes that one call site to `model.astream(prompt_val)`,
accumulating deltas into the same `content` string the existing JSON-parse logic already
consumes — REST callers see no behavior change, still get one final `TrendReport`.
`node_intent_task_planner`'s own JSON-mode `ainvoke` call ([agents/trend/trend_agent.py:102](../../../agents/trend/trend_agent.py#L102))
is left untouched, so it produces no token events — only the final synthesis streams,
not raw intermediate JSON.

**4. Token chunks carry raw JSON-mode deltas, not prose.** The synthesis call is
JSON-mode by design (produces the structured `TrendReport`). Streaming it token-by-token
means the client sees JSON assembling live, not a narrative typing effect. A client that
wants readable live text needs to render/parse that itself. Adding a second, separate
prose-only LLM call purely for a nicer typing effect was considered and rejected —
doubles LLM calls per request for a cosmetic gain.

## Message protocol

Reuses the existing `StreamChunk` model ([agents/universal-agent/api/schemas.py:157-220](../../../agents/universal-agent/api/schemas.py#L157))
verbatim — no new chunk types.

**Client → server**, first frame after connecting:
```json
{"request": "latest AI technology trends", "company_id": "optional-uuid", "token": "optional-if-not-in-query-or-header"}
```

**Server → client:**
- `status` — one per graph node as it starts. `metadata={"step": "<node_name>"}`.
  Node → message: `intent_task_planner` "Analyzing request intent...",
  `capability_planner` "Planning data capabilities...", `provider_selector`
  "Selecting data providers...", `fetch_providers` "Fetching trend data...",
  `normalize_data` "Normalizing results...", `remove_duplicates` "Removing duplicates...",
  `rank_trends` "Ranking trends...", `llm_trend_analysis` "Synthesizing report...".
- `token` — one per delta from the synthesis call only. `content=<raw JSON delta>`.
- `done` — final frame. `content=""`, `metadata=<AgentResponse.model_dump()>`. Same
  convention already used by `generate_content_simple_stream` and
  `generate_image_from_query_stream`.
- `error` — malformed input, business-context load failure, or unhandled exception.
  Connection closes with code 1008 after (matches existing sibling routes), except for a
  business-context load failure, which is a normal agent-level failure (mirrors what
  `TrendAgent.run` would put in `AgentResponse.errors`) and closes normally, not 1008.

**Cache hit path:** check `agents.trend.cache.in_memory_cache` first, same key scheme
`TrendAgent.run` already uses (`f"{company_id or 'default'}:{request}"`). On hit: send
`status(step="cache_hit")`, then `done` with the cached `AgentResponse`, close. No graph
execution.

## Error handling

- Malformed/missing first JSON frame, or empty `request` → `error` chunk, close 1008.
- `context_manager.get_business_context` raises → `error` chunk with the same message
  `TrendAgent.run` would surface, close (not 1008).
- Exception during `astream_events` iteration → `error` chunk, close 1008.
- Client disconnects mid-stream → catch `WebSocketDisconnect`, stop iterating (`aclose()`
  the async generator), let the partial run end there. No background continuation to
  warm the cache after abandonment — out of scope, add later if it's ever needed.

## Testing

One `pytest` file using a `FakeWebSocket` harness, matching the existing convention in
`agents/universal-agent/api/test_subagent_stream_close.py` (this codebase has no live
WebSocket client test scripts anywhere — a real client script was considered per the
draft plan and rejected in favor of the established convention). Covers: cache-hit
short-circuit, the 8-node status sequence in order, token accumulation into the final
report, `done` payload shape, and the error/close-code paths (malformed frame, empty
request, business-context failure, mid-stream disconnect).

## Out of scope

- Session replay / reconnect-and-resume for trend runs (see Decision 2).
- Continuing a run in the background after client disconnect to still populate the cache.
- Streaming node-internal progress finer than "node started" (e.g. per-provider fetch
  progress inside `fetch_providers`).
