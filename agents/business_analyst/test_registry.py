import uuid
import pytest
from sqlalchemy.exc import IntegrityError

from agents.business_analyst.capability import CapabilityKind, CapabilitySpec
from agents.business_analyst.registry import (
    get_capability,
    get_ontology_type,
    list_capabilities,
    list_ontology_types,
    register_capability,
    register_ontology_type,
    seed_standard_capabilities,
)


def _unique_key(base: str) -> str:
    """Global catalog rows are seeded and committed (seed_all_ba_catalogs), so a test that registers
    a *global* key must not reuse a seeded one, or the partial unique index rejects it."""
    return f"{base}_{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_seed_standard_capabilities(db_session):
    """Asserts seed_standard_capabilities registers all 14 acquisition and derivation capabilities into the catalog."""
    seeded = await seed_standard_capabilities(db_session, org_id="org_seed_test")
    assert len(seeded) == 14

    # Verify all 14 capability keys exist
    caps = await list_capabilities(db_session, org_id="org_seed_test")
    cap_keys = {c.key for c in caps}

    expected_keys = {
        "document_analysis", "interview", "compliance_lookup", "market_research",
        "derive_requirements", "model_process", "derive_edge_cases", "derive_nfr",
        "derive_data_model", "derive_stakeholders", "derive_raci", "derive_risks",
        "derive_glossary_terms", "derive_options"
    }

    assert expected_keys.issubset(cap_keys)



@pytest.mark.asyncio
async def test_register_global_and_tenant_capabilities(db_session):
    key = _unique_key("derive_requirements")
    # Register global capability
    global_cap = await register_capability(
        db_session,
        key=key,
        kind="derivation",
        name="Global Requirement Derivation",
        org_id=None,
        description="Global version",
    )
    assert global_cap.id is not None
    assert global_cap.org_id is None

    # Register tenant capability (override)
    tenant_cap = await register_capability(
        db_session,
        key=key,
        kind="derivation",
        name="Org 1 Requirement Derivation",
        org_id="org-1",
        description="Tenant specific version",
    )
    assert tenant_cap.id is not None
    assert tenant_cap.org_id == "org-1"


@pytest.mark.asyncio
async def test_partial_unique_index_enforcement_capability(db_session):
    key = _unique_key("doc_analysis")
    # Register global capability
    await register_capability(
        db_session, key=key, kind="acquisition", name="Doc Analysis", org_id=None
    )

    # Attempting to register another global capability with the same key must fail
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await register_capability(
                db_session, key=key, kind="acquisition", name="Duplicate Global", org_id=None
            )

    # Register tenant capability for org-1
    await register_capability(
        db_session, key=key, kind="acquisition", name="Org 1 Doc Analysis", org_id="org-1"
    )

    # Attempting to register another tenant capability for the SAME org and key must fail
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await register_capability(
                db_session, key=key, kind="acquisition", name="Duplicate Tenant", org_id="org-1"
            )

    # Registering for a DIFFERENT tenant org-2 succeeds
    cap_org2 = await register_capability(
        db_session, key=key, kind="acquisition", name="Org 2 Doc Analysis", org_id="org-2"
    )
    assert cap_org2.org_id == "org-2"


@pytest.mark.asyncio
async def test_capability_tenant_override_lookup(db_session):
    key = _unique_key("model_process")
    # 1. Global capability
    await register_capability(
        db_session, key=key, kind="derivation", name="Global Process Model", org_id=None
    )

    # 2. Org-1 override
    await register_capability(
        db_session, key=key, kind="derivation", name="Org 1 Process Model", org_id="org-1"
    )

    # Lookup for Org-1 -> returns Org-1 tenant override
    cap_org1 = await get_capability(db_session, key=key, org_id="org-1")
    assert cap_org1 is not None
    assert cap_org1.name == "Org 1 Process Model"
    assert cap_org1.org_id == "org-1"

    # Lookup for Org-2 -> falls back to Global capability
    cap_org2 = await get_capability(db_session, key=key, org_id="org-2")
    assert cap_org2 is not None
    assert cap_org2.name == "Global Process Model"
    assert cap_org2.org_id is None

    # Lookup for list_capabilities for Org-1 -> returns overridden capability
    caps_list = await list_capabilities(db_session, org_id="org-1")
    matching = [c for c in caps_list if c.key == key]
    assert len(matching) == 1
    assert matching[0].name == "Org 1 Process Model"


@pytest.mark.asyncio
async def test_ontology_type_registration_and_tenant_override(db_session):
    key = _unique_key("Goal")
    # Global ontology type
    await register_ontology_type(
        db_session, key=key, name="Global Goal", org_id=None, category="node"
    )

    # Duplicate global key raises IntegrityError
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await register_ontology_type(
                db_session, key=key, name="Duplicate Global Goal", org_id=None, category="node"
            )

    # Org-1 override
    await register_ontology_type(
        db_session, key=key, name="Org 1 Custom Goal", org_id="org-1", category="node"
    )

    # Query Org-1 -> returns tenant override
    onto_1 = await get_ontology_type(db_session, key=key, org_id="org-1")
    assert onto_1.name == "Org 1 Custom Goal"

    # Query Org-2 -> returns global
    onto_2 = await get_ontology_type(db_session, key=key, org_id="org-2")
    assert onto_2.name == "Global Goal"


def test_capability_spec_from_model():
    from agents.business_analyst.models import BaCapability

    model = BaCapability(
        id="c1",
        key="derive_requirements",
        kind="derivation",
        name="Derive Req",
        org_id=None,
        is_side_effecting=False,
    )
    spec = CapabilitySpec.from_model(model)
    assert spec.key == "derive_requirements"
    assert spec.kind == CapabilityKind.DERIVATION
    assert spec.is_side_effecting is False
