"""Jev clarification selection without the BA database fixture."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agents.business_analyst.clarification import get_next_clarification, gap_subject_key
from agents.business_analyst.facts import BATenantContext
from agents.business_analyst import jev_client


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,expected_field", [(1, "owner"), (RuntimeError("offline"), "description")])
async def test_jev_selection_and_fallback(decision, expected_field):
    ctx = BATenantContext(org_id="org", project_id="project")
    nodes = [
        {"id": "req:first", "type": "Requirement", "attrs": {"description": None}},
        {"id": "req:second", "type": "Requirement", "attrs": {"owner": None}},
    ]
    with patch("agents.business_analyst.clarification.fetch_prompt", new_callable=AsyncMock) as prompt, \
         patch("agents.business_analyst.clarification.choose_clarification_gap", new_callable=AsyncMock) as choose, \
         patch("agents.business_analyst.clarification.phrase_clarification_question", new_callable=AsyncMock) as phrase, \
         patch("agents.business_analyst.clarification.get_facts", new_callable=AsyncMock) as facts, \
         patch("agents.business_analyst.clarification._get_or_create_system_source", new_callable=AsyncMock) as source, \
         patch("agents.business_analyst.clarification.assert_fact", new_callable=AsyncMock):
        prompt.return_value = "Choose the most useful gap"
        if isinstance(decision, Exception):
            choose.side_effect = decision
        else:
            choose.return_value = decision
        phrase.return_value = "Please clarify this field."
        facts.return_value = []
        source.return_value.id = "source-1"
        result = await get_next_clarification(ctx, object(), nodes, [])

    assert result is not None
    assert result["field"] == expected_field
    expected_node = "req:second" if expected_field == "owner" else "req:first"
    assert result["gap_key"] == gap_subject_key(expected_node, expected_field)
    choose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence,expected", [(0.9, 1), (0.4, None)])
async def test_jev_client_accepts_only_confident_valid_choice(monkeypatch, confidence, expected):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "answers": {"next_gap": {
                "choice": "gap_1",
                "confidence": confidence,
                "probabilities": {"gap_0": 0.1, "gap_1": 0.9},
            }}
        })

    client_type = httpx.AsyncClient
    monkeypatch.setattr(jev_client.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    monkeypatch.setattr(jev_client.os, "getenv", lambda name: "test-key" if name == "OPENROUTER_API_KEY" else None)
    gaps = [
        {"node_id": "a", "field": "description", "node_type": "Requirement"},
        {"node_id": "b", "field": "owner", "node_type": "Requirement"},
    ]
    result = await jev_client.choose_clarification_gap(gaps, [], "Choose the next gap")

    assert result == expected
    assert len(requests) == 1
    assert str(requests[0].url) == jev_client.OPENROUTER_DECISIONS_URL
    assert b'"model":"typesafe/jev-1.13"' in requests[0].content
