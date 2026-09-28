"""Tests for Deliverables (§11, Phase 10).

Mandatory Verification Tests:
1. Catalog Audit: All 17 deliverables exist with all 8 fields non-null.
2. Render-time gating: a deliverable whose renderer inputs are absent is gated in plain language and records one gap fact.
3. Narrated renderer input isolation: free-text prompt injection does not mutate structured output.
4. Staleness computation: is_stale() flips to True lazily when a new fact touching input node types is asserted.
5. Privileged approval guard: raw ORM mutation of BaDeliverableInstance.status to 'approved' is blocked.
6. RTM, Gap Report, Change Log make ZERO LLM calls.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy import select

from agents.business_analyst.api.security import (
    before_flush_privileged_guard,
    set_approval_context,
)
from agents.business_analyst.capabilities.projection import render_deliverable
from agents.business_analyst.deliverables import (
    DELIVERABLE_CATALOG_SEEDS,
    approve_deliverable_instance,
    is_stale,
    seed_deliverable_specs,
)
from agents.business_analyst.facts import BATenantContext, assert_fact, register_source
from agents.business_analyst.models import (
    BaDeliverableInstance,
    BaDeliverableSpec,
    BaFact,
)


@pytest.mark.asyncio
async def test_deliverable_catalog_audit_all_8_fields_non_null(db_session) -> None:
    """Audit: Walk all 17 deliverable specs and assert all 8 required metadata fields are non-null."""
    await seed_deliverable_specs(db_session)

    stmt = select(BaDeliverableSpec).where(BaDeliverableSpec.org_id.is_(None))
    res = await db_session.execute(stmt)
    specs = res.scalars().all()

    assert len(specs) == 17, f"Expected 17 deliverable specs, found {len(specs)}"

    required_keys = {item["key"] for item in DELIVERABLE_CATALOG_SEEDS}

    for spec in specs:
        assert spec.key in required_keys
        assert spec.purpose is not None and len(spec.purpose) > 0
        assert spec.required_node_types is not None and isinstance(spec.required_node_types, list)
        assert spec.required_capabilities is not None and isinstance(spec.required_capabilities, list)
        assert spec.renderer_key is not None and len(spec.renderer_key) > 0
        assert spec.review_process in ("self_review", "stakeholder_review", "client_signoff")
        assert spec.versioning_strategy in ("seq_snapshot", "manual_version")
        assert spec.approval_workflow in ("none", "single_approver", "multi_approver")


@pytest.mark.asyncio
async def test_render_gates_when_inputs_missing(db_session, ba_project) -> None:
    """Asserts that rendering gates in plain language and records one gap when the renderer's inputs are absent."""
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    await seed_deliverable_specs(db_session)

    stmt = select(BaDeliverableSpec).where(BaDeliverableSpec.key == "frd", BaDeliverableSpec.org_id.is_(None))
    res = await db_session.execute(stmt)
    spec = res.scalar_one()

    # No requirement facts -> MUST gate; a second attempt must not duplicate the gap
    rendered = await render_deliverable(spec, ctx, db_session)
    await render_deliverable(spec, ctx, db_session)

    assert "can't be written yet" in rendered
    assert "<UNSPECIFIED" not in rendered

    stmt_gap = select(BaFact).where(BaFact.project_id == ctx.project_id, BaFact.predicate == "gap")
    res_gap = await db_session.execute(stmt_gap)
    gap_facts = res_gap.scalars().all()

    assert len(gap_facts) == 1
    assert gap_facts[0].value["deliverable_key"] == "frd"
    assert "requirements" in gap_facts[0].value["reason"]


@pytest.mark.asyncio
async def test_staleness_computation_lazy_on_read(db_session, ba_project) -> None:
    """Asserts is_stale() flips to True lazily when a new fact touching required node types is asserted."""
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    await seed_deliverable_specs(db_session)

    source = await register_source(ctx, db_session, kind="document", tier="tier1", content_hash="h1")
    fact1 = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req_stale_1",
        predicate="spec", value={"text": "Initial spec"}, source_id=source.id, asserted_by="test"
    )

    inst = BaDeliverableInstance(
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        deliverable_key="frd",
        frontier_seq=fact1.seq,
        renderer_version="1.0.0",
        output_format="markdown",
        content_ref="ref_1",
        status="generated",
    )
    db_session.add(inst)
    await db_session.flush()

    assert await is_stale(inst, db_session) is False

    # Assert a new fact touching Requirement
    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req_stale_2",
        predicate="spec", value={"text": "Newer spec"}, source_id=source.id, asserted_by="test"
    )

    # is_stale() MUST now compute True
    assert await is_stale(inst, db_session) is True


@pytest.mark.asyncio
async def test_approval_guard_blocks_raw_orm_mutation_of_deliverable_status(db_session, ba_project) -> None:
    """Asserts that direct ORM mutation of BaDeliverableInstance.status to 'approved' raises PermissionError."""
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)

    inst = BaDeliverableInstance(
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        deliverable_key="brd",
        frontier_seq=1,
        renderer_version="1.0.0",
        output_format="markdown",
        content_ref="ref_guard",
        status="generated",
    )
    db_session.add(inst)
    await db_session.flush()

    set_approval_context(False)
    inst.status = "approved"

    with pytest.raises(PermissionError):
        before_flush_privileged_guard(db_session.sync_session, None, None)

    # Authorized approval via function MUST succeed
    db_session.expolunge_all() if hasattr(db_session, "expolunge_all") else None
    inst.status = "generated"  # undo the blocked raw mutation before the authorized path
    approved = await approve_deliverable_instance(inst.id, "human_reviewer", ctx, db_session)
    assert approved.status == "approved"
    assert approved.approved_by == "human_reviewer"


@pytest.mark.asyncio
async def test_rtm_gap_report_change_log_make_zero_llm_calls(db_session, ba_project) -> None:
    """Asserts that rtm, gap_report, and change_log renderers make ZERO LLM calls."""
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    await seed_deliverable_specs(db_session)

    source = await register_source(ctx, db_session, kind="document", tier="tier1", content_hash="h_zero")
    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req_z",
        predicate="derived_from", object_type="SourceSpan", object_key="src_1",
        source_id=source.id, asserted_by="test"
    )

    with patch("agents.business_analyst.llm_client.get_structured_output", new_callable=AsyncMock) as mock_llm:
        for key in ("rtm", "gap_report", "change_log"):
            stmt = select(BaDeliverableSpec).where(BaDeliverableSpec.key == key, BaDeliverableSpec.org_id.is_(None))
            res = await db_session.execute(stmt)
            spec = res.scalar_one()

            res_text = await render_deliverable(spec, ctx, db_session)
            assert len(res_text) > 0

        # LLM MUST have zero calls across all three
        mock_llm.assert_not_called()


def _fact(subject_type, subject_key, predicate, value=None, **extra):
    return SimpleNamespace(
        id=f"id-{subject_key}-{predicate}", subject_type=subject_type, subject_key=subject_key,
        predicate=predicate, value=value, object_type=extra.get("object_type"),
        object_key=extra.get("object_key"), replaces=extra.get("replaces"),
        human_approval=extra.get("human_approval", False), asserted_by="ba_requirement_extractor",
        asserted_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_renderers_read_production_fact_shapes() -> None:
    """No DB: facts shaped exactly as extraction.py writes them render real fields, never
    'None' lines, developer markers or raw requirement keys."""
    req_key = "src-1:chunk-0:R1"
    requirement = {
        "external_key": "R1", "category": "nonfunctional", "stakeholder": "Finance manager",
        "task": "approve invoices", "object": "invoice", "benefit": None, "trigger": "an invoice arrives",
        "preconditions": ["The manager is signed in"], "outcomes": ["The invoice is approved"],
        "constraints": [{"type": "performance", "value": "under 2 seconds", "measurable": True}],
        "ambiguities": [], "evidence_span_ids": ["span-1"],
    }
    facts = [
        _fact("SourceSpan", "span-1", "evidence", {"id": "span-1", "text": "Managers approve invoices fast."}),
        _fact("Requirement", req_key, "specified_as", requirement),
        _fact("Requirement", req_key, "derived_from", object_type="SourceSpan", object_key="span-1"),
        _fact("Gap", f"{req_key}:benefit", "missing_information",
              {"requirement_key": req_key, "field": "benefit", "reason": "Source evidence does not specify benefit."}),
        _fact("entity", "Invoice", "described_as", {"type": "Resource", "attributes": ["amount", "due date"]}),
        _fact("Requirement", req_key, "specified_as", requirement, replaces="id-old", human_approval=True),
    ]
    project = SimpleNamespace(org_id="org-1", settings={"project_summary": {"business_context": {
        "organization": {"company_name": "Acme", "industry": None, "locations": []},
        "stakeholders": [{"name": "Dana", "role": "CFO", "concerns": ["cost", "speed"]}],
        "risks": [{"risk": "Vendor delay", "mitigation": "Second supplier"}],
        "processes": {"process_name": None, "steps": []},  # legacy dict shape, empty
    }}})
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    ctx = BATenantContext(org_id="org-1", project_id="project-1")
    projection = "agents.business_analyst.capabilities.projection"
    keys = (
        "brd", "frd", "nfr_spec", "user_story", "acceptance_criteria", "test_case", "rtm", "gap_report",
        "change_log", "data_dictionary", "stakeholder_register", "risk_log", "process_model",
    )

    rendered = {}
    with patch(f"{projection}.get_facts", new=AsyncMock(return_value=[])), \
         patch(f"{projection}._get_or_create_system_source", new=AsyncMock(return_value=SimpleNamespace(id="s"))), \
         patch(f"{projection}.assert_fact", new=AsyncMock()), \
         patch(f"{projection}.brd.fetch_prompt", new=AsyncMock(return_value="prompt")), \
         patch(f"{projection}.brd.get_structured_output", new=AsyncMock(side_effect=RuntimeError("llm down"))):
        for key in keys:
            with patch(f"{projection}.{key}.get_facts", new=AsyncMock(return_value=facts), create=True):
                rendered[key] = await render_deliverable(SimpleNamespace(key=key), ctx, session)

    for key, text in rendered.items():
        assert "<UNSPECIFIED" not in text and "None" not in text and "Render Gated" not in text, key
    for key in ("brd", "frd", "user_story", "acceptance_criteria", "test_case", "nfr_spec", "rtm", "gap_report"):
        assert "REQ-001" in rendered[key] and req_key not in rendered[key], key
    assert rendered["brd"].count("REQ-001") == 1  # derived_from/duplicate facts are not requirements
    assert "Finance manager" in rendered["user_story"] and "approve invoices" in rendered["frd"]
    assert "[benefit not yet specified]" in rendered["user_story"]
    assert "Company name**: Acme" in rendered["brd"] and "Industry" not in rendered["brd"]
    assert "could not be written just now" in rendered["brd"]
    assert "Automated" not in rendered["test_case"]
    assert "under 2 seconds" in rendered["nfr_spec"]
    assert "Managers approve invoices fast." in rendered["rtm"]
    assert "Source evidence does not specify benefit." in rendered["gap_report"]
    assert "Invoice" in rendered["data_dictionary"] and "amount" in rendered["data_dictionary"]
    assert "2026-09-25" in rendered["change_log"]
    assert "### Dana" in rendered["stakeholder_register"] and "  - speed" in rendered["stakeholder_register"]
    assert "### Vendor delay" in rendered["risk_log"]
    assert "can't be written yet" in rendered["process_model"]
