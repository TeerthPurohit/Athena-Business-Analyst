"""Tests for Jev finding validity judgment and fail-closed extraction behavior."""

from unittest.mock import AsyncMock, patch
import httpx
import pytest

from agents.business_analyst import jev_client
from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.jev_client import (
    JevUnavailableError,
    judge_findings_validity,
)
from agents.business_analyst.requirements import (
    ExtractedRequirement,
    RequirementCategory,
    RequirementExtraction,
    SourceSpan,
)
from agents.business_analyst.extraction import (
    extract_and_persist_requirements,
    extract_and_persist_facts,
)
from agents.business_analyst.ir import Entity, Goal, Objective, ProjectIR, ProjectScope


@pytest.mark.asyncio
async def test_jev_finding_validity_accepted_and_rejected_batch(monkeypatch):
    """Batch of findings: supported finding is accepted (True), unsupported finding is rejected (False)."""
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "answers": {
                    "finding_0": {
                        "choice": "valid",
                        "confidence": 0.95,
                        "probabilities": {"valid": 0.95, "invalid": 0.05},
                    },
                    "finding_1": {
                        "choice": "invalid",
                        "confidence": 0.90,
                        "probabilities": {"valid": 0.10, "invalid": 0.90},
                    },
                }
            },
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        jev_client.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(
        jev_client.os,
        "getenv",
        lambda name: "test-api-key" if name == "OPENROUTER_API_KEY" else None,
    )

    findings = [
        {"id": "req-1", "claim": "User can view inventory levels", "cited_evidence": ["Line 1: inventory dashboard"]},
        {"id": "req-2", "claim": "User can launch rocket ships", "cited_evidence": ["Line 2: warehouse logistics"]},
    ]
    results = await judge_findings_validity(
        findings,
        evidence="Line 1: inventory dashboard\nLine 2: warehouse logistics",
    )

    assert results["req-1"] is True
    assert results["req-2"] is False
    assert len(requests) == 1
    assert str(requests[0].url) == jev_client.OPENROUTER_DECISIONS_URL
    assert b'"model":"typesafe/jev-1.13"' in requests[0].content


@pytest.mark.parametrize(
    ("confidence", "probability", "accepted"),
    [
        (0.50, 0.50, False),  # low confidence -> rejected (not raised)
        (0.75, 0.65, True),   # both thresholds are inclusive
        (0.80, 0.64, False),  # confident choice but probability below 0.65 -> rejected
        (0.74, 0.90, False),  # probability fine but confidence below 0.75 -> rejected
    ],
)
@pytest.mark.asyncio
async def test_jev_finding_validity_rejects_uncertain_judgments(monkeypatch, confidence, probability, accepted):
    """A "valid" answer counts only when confidence >= 0.75 AND the chosen probability >= 0.65;
    anything less fails closed as False (rejected), it does not raise."""
    def respond(_request):
        return httpx.Response(
            200,
            json={
                "answers": {
                    "finding_0": {
                        "choice": "valid",
                        "confidence": confidence,
                        "probabilities": {"valid": probability, "invalid": round(1 - probability, 2)},
                    }
                }
            },
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        jev_client.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(
        jev_client.os,
        "getenv",
        lambda name: "test-api-key" if name == "OPENROUTER_API_KEY" else None,
    )

    findings = [{"id": "req-uncertain", "claim": "Maybe something happens"}]
    assert await judge_findings_validity(findings, evidence="Some text") == {"req-uncertain": accepted}


@pytest.mark.asyncio
async def test_jev_finding_validity_fails_closed_when_unavailable(monkeypatch):
    """Missing API key or service error raises JevUnavailableError for machine-derived findings."""
    # 1. Missing API key
    monkeypatch.setattr(jev_client.os, "getenv", lambda name: None)
    findings = [{"id": "req-1", "claim": "Supported feature"}]
    with pytest.raises(JevUnavailableError):
        await judge_findings_validity(findings, evidence="Some text")

    # 2. HTTP 500 error from upstream
    monkeypatch.setattr(
        jev_client.os,
        "getenv",
        lambda name: "test-api-key" if name == "OPENROUTER_API_KEY" else None,
    )

    def respond_500(_request):
        return httpx.Response(500, json={"error": "internal error"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        jev_client.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond_500), **kwargs),
    )

    with pytest.raises(JevUnavailableError):
        await judge_findings_validity(findings, evidence="Some text")


@pytest.mark.asyncio
async def test_jev_chat_extraction_fails_closed_when_unavailable(monkeypatch):
    """Planner-derived chat findings cannot bypass Jev just because the source is user text."""
    monkeypatch.setattr(jev_client.os, "getenv", lambda name: None)
    with pytest.raises(JevUnavailableError):
        await judge_findings_validity(
            [{"id": "objective_0", "claim": "Objective: Track warehouse stock"}],
            evidence="We need to track warehouse stock",
        )


@pytest.mark.asyncio
async def test_extract_and_persist_requirements_persists_only_accepted():
    """Accepted requirement is persisted with spans and gaps; rejected requirement and uncited spans are omitted."""
    ctx = BATenantContext(org_id="org-1", project_id="proj-1")
    session = AsyncMock()

    source_text = "Line 1: The inventory manager must approve stock changes.\nLine 2: Ignore this unrelated remark."
    spans = [
        SourceSpan(id="span-1", ordinal=0, start_char=0, end_char=55, text="The inventory manager must approve stock changes."),
        SourceSpan(id="span-2", ordinal=1, start_char=56, end_char=93, text="Ignore this unrelated remark."),
    ]
    req_valid = ExtractedRequirement(
        external_key="inventory-approval",
        category=RequirementCategory.FUNCTIONAL,
        stakeholder="inventory manager",
        task="approve stock changes",
        evidence_span_ids=["span-1"],
    )
    req_rejected = ExtractedRequirement(
        external_key="unrelated-claim",
        category=RequirementCategory.FUNCTIONAL,
        stakeholder="nobody",
        task="fly to mars",
        evidence_span_ids=["span-2"],
    )
    extraction = RequirementExtraction(source_spans=spans, requirements=[req_valid, req_rejected])

    asserted_facts = []

    async def mock_assert_fact(ctx, session, **kwargs):
        asserted_facts.append(kwargs)

    with patch("agents.business_analyst.extraction.segment_source_text", return_value=spans), \
         patch("agents.business_analyst.extraction.build_requirement_extraction_llm", return_value=extraction), \
         patch("agents.business_analyst.extraction.judge_findings_validity", return_value={"inventory-approval": True, "unrelated-claim": False}), \
         patch("agents.business_analyst.extraction.assert_fact", side_effect=mock_assert_fact):

        result = await extract_and_persist_requirements(
            ctx,
            session,
            text=source_text,
            source_id="src-1",
            asserted_by="ba_requirement_extractor",
        )

    # Result counts reflect only accepted requirement
    assert result["requirements_created"] == 1
    assert result["source_spans_created"] == 1
    assert result["gaps_created"] == len(req_valid.missing_fields()) + len(req_valid.ambiguities)

    # Only span-1 was persisted (span-2 cited only by rejected requirement was omitted)
    persisted_spans = [f for f in asserted_facts if f["subject_type"] == "SourceSpan"]
    assert len(persisted_spans) == 1
    assert persisted_spans[0]["subject_key"] == "span-1"

    # Only inventory-approval was persisted
    persisted_reqs = [f for f in asserted_facts if f["subject_type"] == "Requirement" and f["predicate"] == "specified_as"]
    assert len(persisted_reqs) == 1
    assert persisted_reqs[0]["subject_key"] == "src-1:inventory-approval"

    # Derived_from only links to span-1
    persisted_derived = [f for f in asserted_facts if f["subject_type"] == "Requirement" and f["predicate"] == "derived_from"]
    assert len(persisted_derived) == 1
    assert persisted_derived[0]["object_key"] == "span-1"

    # Gaps only exist for inventory-approval
    persisted_gaps = [f for f in asserted_facts if f["subject_type"] == "Gap"]
    for gap in persisted_gaps:
        assert "inventory-approval" in gap["subject_key"]
        assert "unrelated-claim" not in gap["subject_key"]


@pytest.mark.asyncio
async def test_extract_and_persist_requirements_fails_closed_when_jev_unavailable():
    """When Jev is unavailable, extract_and_persist_requirements raises JevUnavailableError and asserts no facts."""
    ctx = BATenantContext(org_id="org-1", project_id="proj-1")
    session = AsyncMock()

    spans = [SourceSpan(id="span-1", ordinal=0, start_char=0, end_char=30, text="Some requirement text here.")]
    req = ExtractedRequirement(
        external_key="req-1",
        category=RequirementCategory.FUNCTIONAL,
        evidence_span_ids=["span-1"],
    )
    extraction = RequirementExtraction(source_spans=spans, requirements=[req])

    asserted_facts = []

    async def mock_assert_fact(ctx, session, **kwargs):
        asserted_facts.append(kwargs)

    with patch("agents.business_analyst.extraction.segment_source_text", return_value=spans), \
         patch("agents.business_analyst.extraction.build_requirement_extraction_llm", return_value=extraction), \
         patch("agents.business_analyst.extraction.judge_findings_validity", side_effect=JevUnavailableError("API key missing")), \
         patch("agents.business_analyst.extraction.assert_fact", side_effect=mock_assert_fact):

        with pytest.raises(JevUnavailableError):
            await extract_and_persist_requirements(
                ctx,
                session,
                text="Some requirement text here.",
                source_id="src-1",
                asserted_by="ba_requirement_extractor",
            )

    # Failed closed: no facts persisted
    assert len(asserted_facts) == 0


@pytest.mark.asyncio
async def test_extract_and_persist_facts_omits_unsupported_and_filters_ir():
    """In fact extraction, unsupported candidate facts are omitted and excluded from returned IR."""
    ctx = BATenantContext(org_id="org-1", project_id="proj-1")
    session = AsyncMock()

    ir = ProjectIR(
        project_name="Inventory",
        objectives=[
            Objective(id="obj-1", description="Valid objective", category="business", priority="high"),
            Objective(id="obj-2", description="Hallucinated objective", category="business", priority="low"),
        ],
        entities=[
            Entity(name="StockItem", type="domain", attributes=["quantity"]),
        ],
        goals=[
            Goal(id="goal-1", name="Accuracy", description="Maintain accuracy", target_metrics=["99.9%"]),
        ],
        scope=ProjectScope(
            in_scope=["Inventory counts", "Interstellar travel"],
            out_of_scope=[],
            constraints=["Must be audited"],
        ),
        decisions=[],
        business_rules=[],
        assumptions=[],
        open_questions=[],
    )

    # Jev judges obj-2 and Interstellar travel as invalid
    judgments = {
        "objective_0": True,
        "objective_1": False,
        "entity_0": True,
        "goal_0": True,
        "scope_in_scope_0": True,
        "scope_in_scope_1": False,
        "scope_constraint_0": True,
    }

    asserted_facts = []

    async def mock_assert_fact(ctx, session, **kwargs):
        asserted_facts.append(kwargs)

    with patch("agents.business_analyst.extraction.build_project_ir_llm", return_value=ir), \
         patch("agents.business_analyst.extraction.judge_findings_validity", return_value=judgments), \
         patch("agents.business_analyst.extraction.assert_fact", side_effect=mock_assert_fact):

        result = await extract_and_persist_facts(
            ctx,
            session,
            text="Evidence text",
            source_id="src-doc-1",
            asserted_by="ba_document_analysis",
        )

    # Valid objective persisted; hallucinated obj-2 omitted
    objective_facts = [f for f in asserted_facts if f["subject_type"] == "goal" and f["predicate"] == "objective"]
    assert len(objective_facts) == 1
    assert objective_facts[0]["subject_key"] == "obj-1"

    # Filtered IR contains only valid items
    filtered_ir: ProjectIR = result["ir"]
    assert len(filtered_ir.objectives) == 1
    assert filtered_ir.objectives[0].id == "obj-1"
    assert filtered_ir.scope.in_scope == ["Inventory counts"]
    assert result["facts_created"] == len(asserted_facts)
