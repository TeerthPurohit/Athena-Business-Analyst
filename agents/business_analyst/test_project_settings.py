"""Tests for BaProject settings CRUD, summary regeneration, and degraded mode (Feature 1)."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from agents.business_analyst.models import BaProject
from agents.business_analyst.project_summary import ProjectSummary, regenerate_project_summary
from agents.business_analyst.facts import BATenantContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def project_with_settings(db_session):
    proj = BaProject(
        id=str(uuid.uuid4()),
        org_id=str(uuid.uuid4()),
        name="Settings Test Project",
        settings={"instructions": "Focus on finance.", "must_have": "GL, AP, AR"},
    )
    db_session.add(proj)
    await db_session.flush()
    return proj


# ---------------------------------------------------------------------------
# Settings CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_project_settings_default_is_empty_dict(db_session):
    """BaProject with no settings kwarg stores an empty dict, not None."""
    proj = BaProject(id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), name="No Settings")
    db_session.add(proj)
    await db_session.flush()
    assert isinstance(proj.settings, dict)
    assert proj.settings == {}


@pytest.mark.asyncio
async def test_project_settings_stores_and_retrieves(db_session, project_with_settings):
    """Settings sub-fields survive a flush and re-fetch."""
    fetched = await db_session.get(BaProject, project_with_settings.id)
    assert fetched.settings["instructions"] == "Focus on finance."
    assert fetched.settings["must_have"] == "GL, AP, AR"


@pytest.mark.asyncio
async def test_project_settings_patch_merges(db_session, project_with_settings):
    """Updating one settings sub-field does not wipe unrelated sub-fields."""
    proj = project_with_settings
    new_settings = dict(proj.settings)
    new_settings["should_have"] = "Multi-currency"
    proj.settings = new_settings
    await db_session.flush()

    fetched = await db_session.get(BaProject, proj.id)
    assert fetched.settings["instructions"] == "Focus on finance."
    assert fetched.settings["should_have"] == "Multi-currency"


@pytest.mark.asyncio
async def test_project_summary_direct_user_edit(db_session, project_with_settings):
    """User can write project_summary directly into settings (same as AI regeneration)."""
    proj = project_with_settings
    user_summary = {
        "vision": "Global ERP leader",
        "mission": "Streamline operations",
        "problem_statement": "Fragmented data",
        "business_goals": "Reduce cost by 20%",
        "icp": "Mid-market manufacturers",
        "project_purpose": "Unified ERP platform",
        "functional_scope": "Finance and procurement",
        "in_scope_features": ["GL", "AP"],
        "out_of_scope_features": ["CRM"],
        "future_enhancements": ["AI forecasting"],
        "key_business_rules": ["Dual approval for POs > $10k"],
        "important_assumptions": ["Single currency in phase 1"],
        "other_context": None,
    }
    new_settings = dict(proj.settings)
    new_settings["project_summary"] = user_summary
    proj.settings = new_settings
    await db_session.flush()

    fetched = await db_session.get(BaProject, proj.id)
    assert fetched.settings["project_summary"]["vision"] == "Global ERP leader"


# ---------------------------------------------------------------------------
# Summary regeneration — happy path (mocked LLM)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_project_summary_happy_path(db_session, project_with_settings):
    """regenerate_project_summary writes summary into settings and stamps summary_updated_at."""
    proj = project_with_settings
    ctx = BATenantContext(org_id=proj.org_id, project_id=proj.id)

    mock_summary = ProjectSummary(
        vision="V", mission="M", problem_statement="PS",
        business_goals="BG", icp="ICP", project_purpose="PP",
        functional_scope="FS", in_scope_features=["A"],
        out_of_scope_features=[], future_enhancements=[],
        key_business_rules=[], important_assumptions=[],
    )

    with patch("agents.business_analyst.project_summary.fetch_prompt", new_callable=AsyncMock) as mock_prompt, \
         patch("agents.business_analyst.project_summary.llm_get_structured_output", new_callable=AsyncMock) as mock_llm:
        mock_prompt.return_value = "system prompt"
        mock_llm.return_value = mock_summary

        result = await regenerate_project_summary(ctx, db_session, proj)

    assert result["vision"] == "V"
    assert proj.settings["project_summary"]["vision"] == "V"
    assert proj.summary_updated_at is not None


# ---------------------------------------------------------------------------
# Degraded mode — LLM failure leaves existing summary untouched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_project_summary_degraded_leaves_existing(db_session, project_with_settings):
    """On LLM failure, existing project_summary is NOT overwritten."""
    proj = project_with_settings
    existing_summary = {"vision": "Original", "mission": "Stay"}
    new_settings = dict(proj.settings)
    new_settings["project_summary"] = existing_summary
    proj.settings = new_settings
    await db_session.flush()

    ctx = BATenantContext(org_id=proj.org_id, project_id=proj.id)

    with patch("agents.business_analyst.project_summary.fetch_prompt", new_callable=AsyncMock) as mock_prompt, \
         patch("agents.business_analyst.project_summary.llm_get_structured_output", new_callable=AsyncMock) as mock_llm:
        mock_prompt.return_value = "system prompt"
        mock_llm.side_effect = RuntimeError("LLM unavailable")

        result = await regenerate_project_summary(ctx, db_session, proj)

    # Summary unchanged
    assert result == existing_summary
    assert proj.settings["project_summary"] == existing_summary


@pytest.mark.asyncio
async def test_regenerate_project_summary_degraded_no_existing_returns_empty(db_session, project_with_settings):
    """On LLM failure with no prior summary, returns empty dict without crashing."""
    proj = project_with_settings
    ctx = BATenantContext(org_id=proj.org_id, project_id=proj.id)

    with patch("agents.business_analyst.project_summary.fetch_prompt", new_callable=AsyncMock), \
         patch("agents.business_analyst.project_summary.llm_get_structured_output", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = RuntimeError("LLM down")
        result = await regenerate_project_summary(ctx, db_session, proj)

    assert result == {}
