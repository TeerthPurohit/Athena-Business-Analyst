from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agents.business_analyst.capabilities.projection import brd, frd
from agents.business_analyst.capabilities.projection.document_template import front_matter, table
from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.models import BaProject, BaSource


@pytest.mark.asyncio
async def test_reference_sections_use_project_evidence_and_scope():
    ctx = BATenantContext(org_id="org", project_id="project")
    fact = SimpleNamespace(subject_type="Requirement", predicate="specified_as", subject_key="private-key", source_id="source", value={
        "task": "Review invoices", "stakeholder": "Accountant", "object": "Invoice",
        "category": "nonfunctional", "outcomes": ["Recorded approval"],
        "constraints": [{"type": "performance", "value": "within 9 seconds", "measurable": True}],
    })
    project = SimpleNamespace(org_id="org", name="Acme", settings={"project_summary": {
        "in_scope_features": ["Invoice review"], "out_of_scope_features": ["Payroll"],
        "business_context": {"data": {"data_retention": "180 days"}},
    }})
    source = SimpleNamespace(org_id="org", project_id="project", ref="C:/private/brief.md", kind="document")
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, key: project if model is BaProject else source))
    with patch.object(brd, "get_facts", AsyncMock(return_value=[fact])), patch.object(frd, "get_facts", AsyncMock(return_value=[fact])), patch.object(brd, "fetch_prompt", AsyncMock(return_value="prompt")), patch.object(brd, "get_structured_output", AsyncMock(return_value=brd.BRDNarrative(executive_summary="Invoice project"))):
        business = await brd.render(None, ctx, session)
        functional = await frd.render(None, ctx, session)
    assert business.index("Document Control") < business.index("Distribution List") < business.index("Table of Contents")
    assert "1.2.2 Out of scope" in business and "Payroll" in business
    assert "2.4.2 Data validation and error management" in business
    assert "4 Annex I - Data fields" in business
    assert "Document Approvals History" in functional
    assert "5.1.1 REQ-001" in functional and "Input Data" in functional and "Failures / Exceptions" in functional
    assert "5.5 Data Dictionary" in functional and "7.0 Appendix A - Glossary" in functional
    assert "180 days" in functional and "within 9 seconds" in functional
    for output in [business, functional]:
        assert "Invoice review" in output and "Payroll" in output
        assert "brief.md" in output and "C:/private" not in output
        assert "private-key" not in output and "Not yet specified" in output
        assert "Global Bank" not in output and "2000 concurrent" not in output


@pytest.mark.asyncio
async def test_reference_list_does_not_disclose_foreign_source():
    ctx = BATenantContext(org_id="org", project_id="project")
    source = SimpleNamespace(org_id="foreign", project_id="project", ref="secret.md", kind="document")
    session = SimpleNamespace(get=AsyncMock(return_value=source))
    _, refs = await front_matter("BRD", "Acme", {}, {}, [SimpleNamespace(source_id="foreign-source")], ctx, session)
    assert "secret.md" not in refs


def test_table_escapes_evidence_without_splitting_columns():
    output = table(["Item", "Value"], [("Rule", "a | b\nnext")])
    assert "a \\| b<br>next" in output
