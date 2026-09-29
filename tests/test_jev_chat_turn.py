"""Turn routing keeps Jev's decisions bounded and uses one request per turn."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import BackgroundTasks

from agents.business_analyst import jev_client
from agents.business_analyst.api import routes
from agents.business_analyst.facts import BATenantContext


def _answer(choice: str, confidence: float = 0.9, probability: float = 0.9) -> dict:
    return {"choice": choice, "confidence": confidence, "probabilities": {choice: probability}}


def test_malformed_choice_is_ignored():
    assert jev_client._confident_choice(
        {"choice": ["record"], "confidence": 1, "probabilities": {}},
        {"record": "Record a project fact."},
    ) is None


@pytest.mark.asyncio
async def test_turn_decisions_are_batched_and_use_recent_context(monkeypatch):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"answers": {
            "chat_action": _answer("record"),
            "user_tone": _answer("frustrated"),
            "topic_shift": _answer("changed"),
            "next_step": _answer("probe"),
        }})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(jev_client.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    monkeypatch.setattr(jev_client.os, "getenv", lambda name: "test-key" if name == "OPENROUTER_API_KEY" else None)

    decision = await jev_client.decide_chat_turn(
        "Actually, the checkout is broken and this is frustrating.", {}, "Who owns onboarding?",
        [{"user": "Earlier topic", "assistant": "Tell me about onboarding."}],
    )

    assert decision == jev_client.ChatTurnDecision("record", "frustrated", "changed", "move_on")
    assert len(requests) == 1
    assert set(requests[0]["questions"]) == {"chat_action", "user_tone", "topic_shift", "next_step"}
    assert requests[0]["state"]["recent_turns"][0]["user"] == "Earlier topic"


@pytest.mark.asyncio
async def test_greeting_does_not_probe_and_uncertain_labels_are_ignored(monkeypatch):
    def respond(_request):
        return httpx.Response(200, json={"answers": {
            "chat_action": _answer("greeting"),
            "user_tone": _answer("frustrated", confidence=0.3),
            "topic_shift": _answer("changed", probability=0.2),
            "next_step": _answer("probe"),
        }})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(jev_client.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    monkeypatch.setattr(jev_client.os, "getenv", lambda name: "test-key" if name == "OPENROUTER_API_KEY" else None)

    decision = await jev_client.decide_chat_turn("Hi!", {})

    assert decision == jev_client.ChatTurnDecision("greeting", "neutral", "same", "move_on")


@pytest.mark.asyncio
async def test_turn_decision_degrades_without_jev(monkeypatch):
    monkeypatch.setattr(jev_client.os, "getenv", lambda _name: None)
    assert await jev_client.decide_chat_turn("Hello", {}) == jev_client.ChatTurnDecision()


@pytest.mark.asyncio
async def test_greeting_route_replies_without_extracting_project_facts(monkeypatch):
    db = AsyncMock()
    specs = MagicMock()
    specs.scalars.return_value.all.return_value = []
    db.execute.return_value = specs
    decide = AsyncMock(return_value=jev_client.ChatTurnDecision("greeting", "neutral", "same", "move_on"))
    greet = AsyncMock(return_value="Hi! What would you like to work through?")
    source = AsyncMock(return_value=SimpleNamespace(id="chat-source"))
    assert_fact = AsyncMock()
    extract = AsyncMock()
    monkeypatch.setattr(routes, "decide_chat_turn", decide)
    monkeypatch.setattr(routes, "_greeting_reply", greet)
    monkeypatch.setattr(routes, "_get_or_create_chat_source", source)
    monkeypatch.setattr(routes, "assert_fact", assert_fact)
    monkeypatch.setattr(routes, "_extract_and_regenerate_summary", extract)

    result = await routes.classify_chat_intent_endpoint(
        "project", routes.ChatMessageRequest(message="Hello!"),
        BATenantContext(org_id="org", project_id="project"), db,
    )

    assert result["action"] == "greeting"
    assert result["reply"] == "Hi! What would you like to work through?"
    assert_fact.assert_awaited_once()
    db.commit.assert_awaited_once()
    extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_on_after_clarification_answer_does_not_ask_another_question(monkeypatch):
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(settings={})
    record = AsyncMock(return_value={"answer": "Ava owns it", "conflict_notice": None})
    rank = AsyncMock()
    choose = AsyncMock()
    monkeypatch.setattr(routes, "record_clarification_answer", record)
    monkeypatch.setattr(routes, "_load_graph_for_ranking", rank)
    monkeypatch.setattr(routes, "get_next_clarification", choose)

    result = await routes.answer_clarification_endpoint(
        "project", "owner-gap",
        routes.ClarificationAnswerRequest(answer="Ava owns it", next_step="move_on"),
        BackgroundTasks(), BATenantContext(org_id="org", project_id="project"), db,
    )

    assert result["next_question"] is None
    rank.assert_not_awaited()
    choose.assert_not_awaited()
