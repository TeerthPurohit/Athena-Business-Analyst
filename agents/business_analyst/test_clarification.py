"""Tests for clarification.py (Feature 3): phrasing, dedup, conflict detection."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from agents.business_analyst.clarification import (
    ClarificationQuestion,
    ConflictNotice,
    gap_subject_key,
    phrase_clarification_question,
    phrase_conflict_notice,
)
from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.models import BaProject


# ---------------------------------------------------------------------------
# gap_subject_key — deterministic hashing
# ---------------------------------------------------------------------------

def test_gap_subject_key_deterministic():
    """Same inputs always produce same key."""
    k1 = gap_subject_key("req:login", "description")
    k2 = gap_subject_key("req:login", "description")
    assert k1 == k2


def test_gap_subject_key_different_for_different_inputs():
    """Different node/field combos produce different keys."""
    k1 = gap_subject_key("req:login", "description")
    k2 = gap_subject_key("req:login", "priority")
    k3 = gap_subject_key("nfr:perf", "description")
    assert k1 != k2
    assert k1 != k3


def test_gap_subject_key_length_is_32():
    """Subject key is always exactly 32 hex chars (sha256[:32])."""
    key = gap_subject_key("some_node", "some_field")
    assert len(key) == 32


# ---------------------------------------------------------------------------
# phrase_clarification_question — mocked LLM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phrase_clarification_question_uses_llm():
    """phrase_clarification_question returns LLM-generated question when LLM succeeds."""
    gap = {"node_id": "req:x", "node_type": "Requirement", "field": "description",
           "placeholder": "description", "kind": "missing_value"}

    with patch("agents.business_analyst.clarification.fetch_prompt", new_callable=AsyncMock) as mock_prompt, \
         patch("agents.business_analyst.clarification.llm_get_structured_output", new_callable=AsyncMock) as mock_llm:
        mock_prompt.return_value = "system prompt"
        mock_llm.return_value = ClarificationQuestion(question="What is the intended user flow?")

        result = await phrase_clarification_question(gap)

    assert result == "What is the intended user flow?"
    mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_phrase_clarification_question_degraded_mode():
    """On LLM failure, returns a templated fallback assembled from gap fields — not a hardcoded string."""
    gap = {"node_id": "req:x", "node_type": "Requirement", "field": "priority",
           "placeholder": "priority", "kind": "missing_value"}

    with patch("agents.business_analyst.clarification.fetch_prompt", new_callable=AsyncMock), \
         patch("agents.business_analyst.clarification.llm_get_structured_output", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = RuntimeError("LLM down")
        result = await phrase_clarification_question(gap)

    # Degraded fallback is assembled from gap fields
    assert "priority" in result
    assert "Requirement" in result


# ---------------------------------------------------------------------------
# phrase_conflict_notice — mocked LLM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phrase_conflict_notice_uses_llm():
    """phrase_conflict_notice returns LLM-generated notice when LLM succeeds."""
    conflict = {"prior": "Yes, always", "new": "No, never"}

    with patch("agents.business_analyst.clarification.fetch_prompt", new_callable=AsyncMock) as mock_prompt, \
         patch("agents.business_analyst.clarification.llm_get_structured_output", new_callable=AsyncMock) as mock_llm:
        mock_prompt.return_value = "system prompt"
        mock_llm.return_value = ConflictNotice(notice="Please confirm: prior was 'Yes, always' but you said 'No, never'.")

        result = await phrase_conflict_notice(conflict)

    assert "prior" in result.lower() or "confirm" in result.lower()


@pytest.mark.asyncio
async def test_phrase_conflict_notice_degraded_mode():
    """On LLM failure, returns a templated fallback with both answers."""
    conflict = {"prior": "Budget is fixed", "new": "Budget is flexible"}

    with patch("agents.business_analyst.clarification.fetch_prompt", new_callable=AsyncMock), \
         patch("agents.business_analyst.clarification.llm_get_structured_output", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = RuntimeError("unavailable")
        result = await phrase_conflict_notice(conflict)

    assert "Budget is fixed" in result
    assert "Budget is flexible" in result


# ---------------------------------------------------------------------------
# BaFact-based dedup via get_next_clarification (integration)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_next_clarification_resumes_already_asked(db_session):
    """A pending question is returned again without adding another asked fact."""
    from agents.business_analyst.clarification import get_next_clarification
    from agents.business_analyst.facts import assert_fact, BATenantContext
    from agents.business_analyst.models import BaProject, BaSource

    org_id = str(uuid.uuid4())
    proj_id = str(uuid.uuid4())

    proj = BaProject(id=proj_id, org_id=org_id, name="Dedup Test")
    db_session.add(proj)

    source = BaSource(
        id=str(uuid.uuid4()), project_id=proj_id, org_id=org_id,
        kind="system", tier="internal", ref="test",
        content_hash="abc123",
    )
    db_session.add(source)
    await db_session.flush()

    ctx = BATenantContext(org_id=org_id, project_id=proj_id)

    # Only one node/gap — record it as already asked
    nodes = [{"id": "req:only", "type": "Requirement", "attrs": {"description": None}}]
    edges = []
    subj_key = gap_subject_key("req:only", "description")

    await assert_fact(
        ctx, db_session,
        subject_type="ClarificationQuestion",
        subject_key=subj_key,
        predicate="asked",
        source_id=source.id,
        asserted_by="ba_clarification_engine",
        value={"question": "Already asked"},
    )

    with patch("agents.business_analyst.clarification.phrase_clarification_question",
               new_callable=AsyncMock) as mock_phrase:
        mock_phrase.return_value = "New question"
        # All gaps are already asked and unanswered — should return None
        result = await get_next_clarification(ctx, db_session, nodes, edges)

    assert result["question"] == "Already asked"
    assert result["gap_key"] == subj_key
    assert result["resumed"] is True
    mock_phrase.assert_not_called()


@pytest.mark.asyncio
async def test_get_next_clarification_skips_answered_gap(db_session):
    """An answered gap stays resolved while its source requirement is unchanged."""
    from agents.business_analyst.clarification import get_next_clarification
    from agents.business_analyst.facts import assert_fact, BATenantContext
    from agents.business_analyst.models import BaProject, BaSource

    org_id = str(uuid.uuid4())
    proj_id = str(uuid.uuid4())

    proj = BaProject(id=proj_id, org_id=org_id, name="Resurface Test")
    db_session.add(proj)

    source = BaSource(
        id=str(uuid.uuid4()), project_id=proj_id, org_id=org_id,
        kind="system", tier="internal", ref="test", content_hash="def456",
    )
    db_session.add(source)
    await db_session.flush()

    ctx = BATenantContext(org_id=org_id, project_id=proj_id)

    nodes = [{"id": "req:resurface", "type": "Requirement", "attrs": {"description": None}}]
    edges = []
    subj_key = gap_subject_key("req:resurface", "description")

    # Both asked and answered exist
    await assert_fact(ctx, db_session, subject_type="ClarificationQuestion",
                      subject_key=subj_key, predicate="asked",
                      source_id=source.id, asserted_by="test", value={"question": "Q?"})
    await assert_fact(ctx, db_session, subject_type="ClarificationQuestion",
                      subject_key=subj_key, predicate="answered",
                      source_id=source.id, asserted_by="test", value={"answer": "A"})

    with patch("agents.business_analyst.clarification.phrase_clarification_question",
               new_callable=AsyncMock) as mock_phrase, \
         patch("agents.business_analyst.clarification._get_or_create_system_source",
               new_callable=AsyncMock) as mock_src:
        mock_phrase.return_value = "Resurface question"
        mock_src.return_value = source

        result = await get_next_clarification(ctx, db_session, nodes, edges)

    assert result is None
    mock_phrase.assert_not_called()
