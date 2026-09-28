"""Tests for BaIndustryTemplate model, registry functions, and seeding (Feature 2)."""
import uuid
import pytest
import pytest_asyncio

from agents.business_analyst.models import BaIndustryTemplate
from agents.business_analyst.registry import (
    get_industry_template,
    list_industry_templates,
    register_industry_template,
    seed_standard_industry_templates,
    STANDARD_INDUSTRY_TEMPLATES,
)


# ---------------------------------------------------------------------------
# Register + Get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_global_industry_template(db_session):
    """Registers a global template (org_id=None) and retrieves it."""
    tpl = await register_industry_template(
        db_session, key="test_erp", name="Test ERP", org_id=None,
        description="A test ERP template",
        default_instructions="Focus on finance.",
    )
    assert tpl.id
    assert tpl.org_id is None

    fetched = await get_industry_template(db_session, "test_erp")
    assert fetched is not None
    assert fetched.name == "Test ERP"
    assert fetched.default_instructions == "Focus on finance."


@pytest.mark.asyncio
async def test_tenant_override_wins_over_global(db_session):
    """Tenant template overrides global when org_id is supplied."""
    org_id = str(uuid.uuid4())

    await register_industry_template(db_session, key="crm_x", name="Global CRM", org_id=None,
                                     default_instructions="Global instructions")
    await register_industry_template(db_session, key="crm_x", name="Tenant CRM", org_id=org_id,
                                     default_instructions="Tenant-specific instructions")

    result = await get_industry_template(db_session, "crm_x", org_id=org_id)
    assert result.org_id == org_id
    assert result.default_instructions == "Tenant-specific instructions"


@pytest.mark.asyncio
async def test_get_global_when_no_tenant_override(db_session):
    """Falls back to global template when no tenant override exists."""
    org_id = str(uuid.uuid4())
    await register_industry_template(db_session, key="hrms_x", name="Global HRMS", org_id=None)

    result = await get_industry_template(db_session, "hrms_x", org_id=org_id)
    assert result is not None
    assert result.org_id is None


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_key(db_session):
    """Returns None for a key not in the DB."""
    result = await get_industry_template(db_session, "nonexistent_key_abc123")
    assert result is None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_industry_templates_resolves_overrides(db_session):
    """list_industry_templates returns tenant row instead of global for overridden keys."""
    org_id = str(uuid.uuid4())
    await register_industry_template(db_session, key="edu_x", name="Global EDU", org_id=None)
    await register_industry_template(db_session, key="edu_x", name="Tenant EDU", org_id=org_id)

    results = await list_industry_templates(db_session, org_id=org_id)
    edu_results = [r for r in results if r.key == "edu_x"]
    assert len(edu_results) == 1
    assert edu_results[0].org_id == org_id


# ---------------------------------------------------------------------------
# Seed — idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_standard_industry_templates_idempotent(db_session):
    """Seeding twice does not raise or create duplicates."""
    seeded_first = await seed_standard_industry_templates(db_session)
    seeded_second = await seed_standard_industry_templates(db_session)
    # Both calls return 6 items; second call should return existing rows
    assert len(seeded_first) == 6
    assert len(seeded_second) == 6
    ids_first = {t.id for t in seeded_first}
    ids_second = {t.id for t in seeded_second}
    assert ids_first == ids_second  # Same rows, not duplicates


@pytest.mark.asyncio
async def test_standard_templates_cover_all_six_keys(db_session):
    """All 6 standard industry template keys are seeded."""
    await seed_standard_industry_templates(db_session)
    expected_keys = {"erp", "crm", "hrms", "healthcare", "banking", "education"}
    for key in expected_keys:
        tpl = await get_industry_template(db_session, key)
        assert tpl is not None, f"Template '{key}' was not seeded"


# ---------------------------------------------------------------------------
# Project creation integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_template_defaults_applied_when_body_omits_fields(db_session):
    """Template defaults fill in None fields — explicit body values win."""
    org_id = str(uuid.uuid4())
    await register_industry_template(
        db_session, key="tpl_proj", name="Test Template", org_id=None,
        default_instructions="Template default instructions",
        default_must_have="Template must-have",
        default_should_have="Template should-have",
    )

    # Simulate project creation logic: body provides instructions, omits others
    body_instructions = "User-provided instructions"
    body_must_have = None
    body_should_have = None

    tpl = await get_industry_template(db_session, "tpl_proj", org_id=org_id)
    instructions = body_instructions or (tpl.default_instructions if tpl else None)
    must_have = body_must_have or (tpl.default_must_have if tpl else None)
    should_have = body_should_have or (tpl.default_should_have if tpl else None)

    # Explicit body value wins
    assert instructions == "User-provided instructions"
    # Template defaults fill in
    assert must_have == "Template must-have"
    assert should_have == "Template should-have"
