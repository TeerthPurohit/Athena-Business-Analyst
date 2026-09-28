# Trend Search WebSocket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/ws/trends/search`, a WebSocket sibling to `POST /api/trends/search`, that streams a status update per LangGraph node and the final report token-by-token, then a final result frame — without touching the existing REST behavior.

**Architecture:** The synthesis LLM call inside `TrendService.analyze_trends` switches from `model.ainvoke()` to `model.astream()` (REST output unchanged — same accumulated content). The new WebSocket route drives `trend_agent.graph.astream_events(initial_state, version="v2")` directly inside its own coroutine — no Redis relay, no background task — dispatching `on_chain_start`/`on_chain_end` events to `StreamChunk(type="status")` frames and `on_chat_model_stream` events to `StreamChunk(type="token")` frames, then assembles and sends the final `AgentResponse` as `StreamChunk(type="done")`.

**Tech Stack:** FastAPI WebSocket, LangGraph 1.2.9 `astream_events` v2, LangChain `ChatOpenAI.astream`, existing `StreamChunk` schema, `pytest` + `pytest-asyncio`.

## Global Constraints

- No new chunk types — reuse `StreamChunk` (`agents/universal-agent/api/schemas.py:157-220`) as-is.
- Auth is soft-fail, matching `get_current_company_id` (`app/api.py:67-100`) and every existing WS route in `app/main.py` — never hard-close the socket over a missing/invalid token.
- No `publish_to_stream`/Redis/`_relay_brain_stream` — this endpoint drives the graph directly in its own coroutine.
- REST `POST /api/trends/search` behavior must be byte-for-byte unchanged after this work.
- Full design rationale lives in `docs/superpowers/specs/2026-07-30-trend-search-websocket-design.md` — consult it for the "why" behind any of the above.

---

### Task 1: Stream the synthesis LLM call in `TrendService.analyze_trends`

**Files:**
- Modify: `agents/trend/trend_service.py:216-219`
- Test: `agents/trend/test_trend_service.py` (new)

**Interfaces:**
- Consumes: `agents.shared.llm_client.get_chat_model(use_json_mode: bool, user_id: Optional[str], stream_usage: bool) -> tuple[BaseChatModel, dict]` (already accepts `stream_usage` as a passthrough override — see `agents/shared/llm_client.py:29-67`, `valid_openai_keys` includes `"stream_usage"`).
- Produces: `TrendService.analyze_trends(...)` keeps its existing signature and return type (`TrendReport`) — no caller changes needed. The only observable internal change is that the LLM call is now streamed and accumulated before parsing, instead of one blocking call.

- [ ] **Step 1: Write the failing test**

Create `agents/trend/test_trend_service.py`:

```python
import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.prompts import ChatPromptTemplate

from agents.trend.trend_service import trend_service
from agents.trend.schemas import TrendIntent, NormalizedTrend, RankedTrend
from models.business_schemas import BusinessContextSchema, BusinessProfile, ProductInfo, AudienceProfile


def _business_context():
    return BusinessContextSchema(
        business=BusinessProfile(company_name="Acme", industry="Tech", description="Widgets"),
        products=[ProductInfo(product_id="p1", name="Widget", description="A widget", keywords=["widget"])],
        audience=AudienceProfile(target_platforms=["LinkedIn"], demographics={}, interests=["gadgets"]),
    )


def _ranked_trend():
    normalized = NormalizedTrend(
        title="AI agents", description="Everyone is building agents",
        url=None, source_providers=["serper"], first_seen="2026-07-30T00:00:00Z", raw_metrics=[],
    )
    return RankedTrend(trend=normalized, score_breakdown={"freshness": 1.0}, total_score=90.0)


class _FakeStreamingModel:
    """Yields the JSON payload split across several AIMessageChunk deltas,
    like a real streaming OpenAI response would."""

    def __init__(self, json_text: str):
        self._parts = [json_text[i:i + 5] for i in range(0, len(json_text), 5)]

    async def astream(self, _prompt_val):
        for part in self._parts:
            yield AIMessageChunk(content=part)


@pytest.mark.asyncio
async def test_analyze_trends_streams_and_still_parses_full_report(monkeypatch):
    json_text = (
        '{"insights": [], "trending_keywords": ["ai"], '
        '"trending_hashtags": ["#ai"], "summary": "AI agents are trending."}'
    )
    fake_model = _FakeStreamingModel(json_text)

    async def fake_get_chat_model(**kwargs):
        assert kwargs.get("stream_usage") is True, "must request usage on the streamed call"
        return fake_model, {"provider": "openai", "model": "gpt-test"}

    monkeypatch.setattr("agents.trend.trend_service.get_chat_model", fake_get_chat_model)

    async def fake_get_trend_analyst_prompt():
        return ChatPromptTemplate.from_messages([("system", "s"), ("human", "h")])

    monkeypatch.setattr(
        "agents.trend.trend_service.prompt_manager.get_trend_analyst_prompt",
        fake_get_trend_analyst_prompt,
    )

    intent = TrendIntent(
        industry="Tech", search_queries=["ai"], platforms=["LinkedIn"],
        countries=["US"], time_range="7d", user_goal="grow reach",
    )
    report = await trend_service.analyze_trends(
        ranked=[_ranked_trend()], intent=intent, business_context=_business_context(),
    )

    assert report.summary == "AI agents are trending."
    assert report.trending_keywords == ["ai"]
    assert len(report.trends) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/trend/test_trend_service.py -v`
Expected: FAIL — `_FakeStreamingModel` has no effect yet because `analyze_trends` still calls `model.ainvoke(prompt_val)`, which doesn't exist on the fake (AttributeError), or the `stream_usage` assertion never fires because `get_chat_model` is currently called without it.

- [ ] **Step 3: Change `analyze_trends` to stream the synthesis call**

In `agents/trend/trend_service.py`, replace lines 216-219:

```python
        # Query LLM
        model, resolved = await get_chat_model(use_json_mode=True, user_id=user_id)
        response = await model.ainvoke(prompt_val)
        record_trend_llm_usage(response, provider=resolved.get("provider"), model=resolved.get("model"))
```

with:

```python
        # Query LLM — streamed so a caller iterating the graph's astream_events
        # (the /ws/trends/search route) can relay tokens live. REST callers are
        # unaffected: this still produces one accumulated response before parsing.
        model, resolved = await get_chat_model(use_json_mode=True, user_id=user_id, stream_usage=True)
        response = None
        async for chunk in model.astream(prompt_val):
            response = chunk if response is None else response + chunk
        record_trend_llm_usage(response, provider=resolved.get("provider"), model=resolved.get("model"))
```

Everything below (line 220 onward: `content = response.content.strip()` and the rest of the parsing logic) is unchanged — `response` still behaves like a single message with `.content` and `.usage_metadata`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/trend/test_trend_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trend/trend_service.py agents/trend/test_trend_service.py
git commit -m "feat: stream the trend synthesis LLM call for live token relay"
```

---

### Task 2: Add `/ws/trends/search` WebSocket route

**Files:**
- Modify: `app/main.py` (add module-level `_TREND_NODE_MESSAGES` dict and a new `@app.websocket("/ws/trends/search")` handler, placed directly after `search_trends` at `app/main.py:377`)
- Test: `app/test_trend_ws.py` (new)

**Interfaces:**
- Consumes: `trend_agent.graph.astream_events(initial_state: dict, version="v2")` (async generator of LangGraph event dicts — `{"event": str, "name": str, "data": {"chunk": AIMessageChunk} | {"output": dict}}`); `agents.trend.cache.in_memory_cache.get(key: str)` / `.set(key: str, value: AgentResponse)` (`agents/trend/cache.py:1-29`); `agents.trend.context_manager.context_manager.get_business_context(context_id: Optional[str]) -> BusinessContextSchema` (`agents/trend/context_manager.py:23`); `api.security.verify_jwt_token(token: str) -> dict`; `app.api.company_id_from_jwt_payload(payload: Optional[dict]) -> Optional[str]` (already imported at `app/main.py:40`); `StreamChunk` (already imported at `app/main.py:53`); `AgentResponse` (already imported at `app/main.py:27`); `trend_agent` (module-level singleton, `app/main.py:237`).
- Produces: nothing consumed by later tasks — this is the last task in the plan.

- [ ] **Step 1: Write the failing test**

Create `app/test_trend_ws.py`:

```python
"""Hermetic unit tests for /ws/trends/search — calls the route handler
directly as a plain async function against a FakeWebSocket, same pattern as
app/test_brain_routes.py and agents/universal-agent/api/test_subagent_stream_close.py.
"""
import json

import pytest

from app.main import search_trends_stream
from agents.base.agent_response import AgentResponse


class FakeWebSocket:
    def __init__(self, message):
        self._message = message
        self.query_params = {}
        self.headers = {}
        self.sent = []
        self.closed = False
        self.close_code = None

    async def accept(self):
        pass

    async def receive_json(self):
        return self._message

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000, reason=None):
        self.closed = True
        self.close_code = code


@pytest.mark.asyncio
async def test_cache_hit_sends_status_then_done(monkeypatch):
    cached = AgentResponse(agent_id="trend_intelligence", status="success", data=None, errors=[], metadata={})

    async def fake_get(key):
        return cached

    monkeypatch.setattr("agents.trend.cache.in_memory_cache.get", fake_get)

    ws = FakeWebSocket({"request": "AI trends", "company_id": "acme"})
    await search_trends_stream(ws)

    assert [m["type"] for m in ws.sent] == ["status", "done"]
    assert ws.sent[0]["metadata"]["step"] == "cache_hit"
    assert ws.sent[1]["metadata"]["agent_id"] == "trend_intelligence"
    assert ws.closed


@pytest.mark.asyncio
async def test_missing_request_closes_1008():
    ws = FakeWebSocket({})
    await search_trends_stream(ws)

    assert ws.sent[0]["type"] == "error"
    assert ws.closed and ws.close_code == 1008


@pytest.mark.asyncio
async def test_business_context_failure_sends_error_and_closes(monkeypatch):
    async def fake_get(key):
        return None

    monkeypatch.setattr("agents.trend.cache.in_memory_cache.get", fake_get)

    async def fake_get_business_context(context_id=None):
        raise RuntimeError("no such company")

    monkeypatch.setattr(
        "agents.trend.context_manager.context_manager.get_business_context",
        fake_get_business_context,
    )

    ws = FakeWebSocket({"request": "AI trends", "company_id": "ghost-co"})
    await search_trends_stream(ws)

    assert ws.sent[-1]["type"] == "error"
    assert "no such company" in ws.sent[-1]["content"]
    assert ws.closed


@pytest.mark.asyncio
async def test_full_run_streams_status_tokens_then_done(monkeypatch):
    async def fake_cache_get(key):
        return None

    async def fake_cache_set(key, value):
        pass

    monkeypatch.setattr("agents.trend.cache.in_memory_cache.get", fake_cache_get)
    monkeypatch.setattr("agents.trend.cache.in_memory_cache.set", fake_cache_set)

    class _BC:
        pass

    async def fake_get_business_context(context_id=None):
        return _BC()

    monkeypatch.setattr(
        "agents.trend.context_manager.context_manager.get_business_context",
        fake_get_business_context,
    )

    from agents.trend.schemas import TrendReport

    async def fake_astream_events(initial_state, version="v2"):
        yield {"event": "on_chain_start", "name": "intent_task_planner", "data": {}}
        yield {"event": "on_chain_end", "name": "intent_task_planner", "data": {"output": {"intent": "x"}}}
        yield {"event": "on_chain_start", "name": "llm_trend_analysis", "data": {}}

        class _Chunk:
            content = '{"summary'

        yield {"event": "on_chat_model_stream", "name": "llm_trend_analysis", "data": {"chunk": _Chunk()}}

        report = TrendReport(summary="ok", trends=[], insights=[], trending_keywords=[], trending_hashtags=[])
        yield {
            "event": "on_chain_end", "name": "llm_trend_analysis",
            "data": {"output": {"report": report, "errors": []}},
        }

    from app.main import trend_agent

    monkeypatch.setattr(trend_agent.graph, "astream_events", fake_astream_events)

    ws = FakeWebSocket({"request": "AI trends", "company_id": "acme"})
    await search_trends_stream(ws)

    types = [m["type"] for m in ws.sent]
    assert types == ["status", "status", "token", "done"]
    assert ws.sent[2]["content"] == '{"summary'
    assert ws.sent[-1]["metadata"]["status"] == "success"
    assert ws.sent[-1]["metadata"]["data"]["summary"] == "ok"
    assert ws.closed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/test_trend_ws.py -v`
Expected: FAIL with `ImportError: cannot import name 'search_trends_stream' from 'app.main'` — the route doesn't exist yet.

- [ ] **Step 3: Add the node-message map and the route**

In `app/main.py`, directly after the `search_trends` handler (ends at line 377), add:

```python
_TREND_NODE_MESSAGES = {
    "intent_task_planner": "Analyzing request intent and planning tasks...",
    "capability_planner": "Planning data capabilities...",
    "provider_selector": "Selecting data providers...",
    "fetch_providers": "Fetching trend data from providers...",
    "normalize_data": "Normalizing results...",
    "remove_duplicates": "Removing duplicate trends...",
    "rank_trends": "Ranking trends...",
    "llm_trend_analysis": "Synthesizing the trend report...",
}


@app.websocket("/ws/trends/search")
async def search_trends_stream(websocket: WebSocket):
    """Live version of POST /api/trends/search — streams a status update per
    LangGraph node plus the final report token-by-token, instead of one blob
    at the end. Drives the graph directly in this coroutine (no Redis relay):
    this is a single request/response per connection, not a replayable chat
    session, so the publish_to_stream/_relay_brain_stream machinery the other
    WS routes in this file use would be pure overhead here."""
    import uuid as _uuid
    from api.security import verify_jwt_token
    from agents.trend.cache import in_memory_cache
    from agents.trend.context_manager import context_manager

    await websocket.accept()
    try:
        message_data = await websocket.receive_json()
    except Exception:
        await websocket.close(code=1008)
        return

    request_text = (message_data.get("request") or "").strip()
    if not request_text:
        await websocket.send_text(
            StreamChunk(type="error", content="Missing 'request'", session_id="").model_dump_json()
        )
        await websocket.close(code=1008)
        return
    session_id = str(_uuid.uuid4())

    token = message_data.get("token") or websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1]
    payload_jwt = None
    if token:
        try:
            payload_jwt = verify_jwt_token(token)
        except Exception:
            pass

    company_id = (
        message_data.get("company_id")
        or company_id_from_jwt_payload(payload_jwt)
        or os.getenv("DEFAULT_COMPANY_ID", "default_company")
    )
    user_id = message_data.get("user_id") or (payload_jwt.get("sub") if payload_jwt else None)

    try:
        cache_key = f"{company_id or 'default'}:{request_text}"
        cached_res = await in_memory_cache.get(cache_key)
        if cached_res:
            await websocket.send_text(
                StreamChunk(
                    type="status", content="Cache hit! Returning cached report.",
                    session_id=session_id, metadata={"step": "cache_hit"},
                ).model_dump_json()
            )
            await websocket.send_text(
                StreamChunk(
                    type="done", content="", session_id=session_id,
                    metadata=cached_res.model_dump(),
                ).model_dump_json()
            )
            await websocket.close()
            return

        try:
            business_context = await context_manager.get_business_context(company_id)
        except Exception as e:
            await websocket.send_text(
                StreamChunk(
                    type="error", content=f"Context retrieval failed: {str(e)}",
                    session_id=session_id,
                ).model_dump_json()
            )
            await websocket.close()
            return

        initial_state = {
            "user_request": request_text,
            "context_id": company_id,
            "user_id": user_id,
            "business_context": business_context,
            "intent": None,
            "capabilities": [],
            "selected_providers": [],
            "raw_data": [],
            "normalized_trends": [],
            "ranked_trends": [],
            "report": None,
            "errors": [],
        }
        final_state: Dict[str, Any] = dict(initial_state)

        async for event in trend_agent.graph.astream_events(initial_state, version="v2"):
            kind = event.get("event")
            name = event.get("name")
            if kind == "on_chain_start" and name in _TREND_NODE_MESSAGES:
                await websocket.send_text(
                    StreamChunk(
                        type="status", content=_TREND_NODE_MESSAGES[name],
                        session_id=session_id, metadata={"step": name},
                    ).model_dump_json()
                )
            elif kind == "on_chain_end" and name in _TREND_NODE_MESSAGES:
                output = event.get("data", {}).get("output")
                if isinstance(output, dict):
                    for k, v in output.items():
                        # TrendAgentState.errors uses an operator.add reducer
                        # (agents/trend/trend_agent.py:39) — every other key is
                        # last-writer-wins, matching LangGraph's default channel
                        # behavior for keys without an Annotated reducer.
                        if k == "errors" and isinstance(v, list):
                            final_state["errors"] = final_state.get("errors", []) + v
                        else:
                            final_state[k] = v
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                delta = getattr(chunk, "content", "") if chunk else ""
                if delta:
                    await websocket.send_text(
                        StreamChunk(type="token", content=delta, session_id=session_id).model_dump_json()
                    )

        report = final_state.get("report")
        errors = final_state.get("errors", [])
        status = "success"
        if errors and not report:
            status = "failed"
        elif errors:
            status = "partial_success"

        response = AgentResponse(
            agent_id="trend_intelligence",
            status=status,
            data=report,
            errors=errors,
            metadata={
                "request": request_text,
                "providers_selected": final_state.get("selected_providers"),
                "raw_count": len(final_state.get("raw_data", [])),
                "normalized_count": len(final_state.get("normalized_trends", [])),
            },
        )
        if status in ("success", "partial_success"):
            await in_memory_cache.set(cache_key, response)

        await websocket.send_text(
            StreamChunk(
                type="done", content="", session_id=session_id,
                metadata=response.model_dump(),
            ).model_dump_json()
        )
        await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(
                StreamChunk(type="error", content=str(e), session_id=session_id).model_dump_json()
            )
            await websocket.close(code=1008)
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest app/test_trend_ws.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `pytest app/ agents/trend/ -v`
Expected: PASS — in particular, no existing test exercising `POST /api/trends/search` or `TrendService.analyze_trends` should change behavior.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/test_trend_ws.py
git commit -m "feat: add /ws/trends/search streaming endpoint for Trend Intelligence"
```
