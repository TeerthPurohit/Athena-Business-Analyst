"""Catalog Registry for BA OS capabilities, ontology types, and deliverable specifications.

Handles catalog registration, queries, and tenant overrides (§4.3, §11.1).
A tenant row (org_id IS NOT NULL) overrides a global default row (org_id IS NULL) for the same key.
"""
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.deliverables import seed_deliverable_specs
from agents.business_analyst.models import BaCapability, BaOntologyType, BaIndustryTemplate


async def register_capability(
    session: AsyncSession,
    *,
    key: str,
    kind: str,
    name: str,
    org_id: Optional[str] = None,  # None = global
    is_side_effecting: bool = False,
    description: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
    conditions: Optional[List[Dict[str, Any]]] = None,
) -> BaCapability:
    """Registers a capability in the catalog. org_id=None registers a global capability."""
    cap = BaCapability(
        id=str(uuid.uuid4()),
        key=key,
        org_id=org_id,
        kind=kind,
        is_side_effecting=is_side_effecting,
        name=name,
        description=description,
        inputs=inputs,
        outputs=outputs,
        conditions=conditions,
    )
    session.add(cap)
    await session.flush()
    return cap


async def get_capability(
    session: AsyncSession,
    key: str,
    org_id: Optional[str] = None,
) -> Optional[BaCapability]:
    """Gets a capability by key. If org_id is provided, tenant override row takes precedence over global default."""
    if org_id is not None:
        stmt = (
            select(BaCapability)
            .where(BaCapability.key == key)
            .where(or_(BaCapability.org_id == org_id, BaCapability.org_id.is_(None)))
            .order_by(BaCapability.org_id.nulls_last())
        )
    else:
        stmt = select(BaCapability).where(BaCapability.key == key, BaCapability.org_id.is_(None))

    res = await session.execute(stmt)
    return res.scalars().first()


async def list_capabilities(
    session: AsyncSession,
    org_id: Optional[str] = None,
) -> List[BaCapability]:
    """Lists all capabilities, resolving tenant overrides for the given org_id."""
    stmt = (
        select(BaCapability)
        .where(or_(BaCapability.org_id == org_id, BaCapability.org_id.is_(None)))
        .order_by(BaCapability.org_id.nulls_last())
    ) if org_id is not None else select(BaCapability).where(BaCapability.org_id.is_(None))

    res = await session.execute(stmt)
    all_caps = res.scalars().all()

    resolved: Dict[str, BaCapability] = {}
    for cap in all_caps:
        if cap.key not in resolved or cap.org_id is not None:
            resolved[cap.key] = cap
    return list(resolved.values())


async def register_ontology_type(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    org_id: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    schema_def: Optional[Dict[str, Any]] = None,
) -> BaOntologyType:
    """Registers an ontology type in the catalog."""
    onto = BaOntologyType(
        id=str(uuid.uuid4()),
        key=key,
        org_id=org_id,
        name=name,
        description=description,
        category=category,
        schema_def=schema_def,
    )
    session.add(onto)
    await session.flush()
    return onto


async def get_ontology_type(
    session: AsyncSession,
    key: str,
    org_id: Optional[str] = None,
) -> Optional[BaOntologyType]:
    """Gets an ontology type by key, with tenant override resolution."""
    if org_id is not None:
        stmt = (
            select(BaOntologyType)
            .where(BaOntologyType.key == key)
            .where(or_(BaOntologyType.org_id == org_id, BaOntologyType.org_id.is_(None)))
            .order_by(BaOntologyType.org_id.nulls_last())
        )
    else:
        stmt = select(BaOntologyType).where(BaOntologyType.key == key, BaOntologyType.org_id.is_(None))

    res = await session.execute(stmt)
    return res.scalars().first()


async def list_ontology_types(
    session: AsyncSession,
    org_id: Optional[str] = None,
) -> List[BaOntologyType]:
    """Lists all ontology types, resolving tenant overrides."""
    stmt = (
        select(BaOntologyType)
        .where(or_(BaOntologyType.org_id == org_id, BaOntologyType.org_id.is_(None)))
        .order_by(BaOntologyType.org_id.nulls_last())
    ) if org_id is not None else select(BaOntologyType).where(BaOntologyType.org_id.is_(None))

    res = await session.execute(stmt)
    all_ontos = res.scalars().all()

    resolved: Dict[str, BaOntologyType] = {}
    for onto in all_ontos:
        if onto.key not in resolved or onto.org_id is not None:
            resolved[onto.key] = onto
    return list(resolved.values())


STANDARD_CAPABILITIES = [
    {"key": "document_analysis", "kind": "acquisition", "name": "Document Analysis", "is_side_effecting": False, "inputs": {"document_text": "str"}, "outputs": {"raw_facts": "List[Fact]"}},
    {"key": "interview", "kind": "acquisition", "name": "Stakeholder Interview", "is_side_effecting": False, "inputs": {"transcript_text": "str"}, "outputs": {"interview_quotes": "List[Fact]"}},
    {"key": "compliance_lookup", "kind": "acquisition", "name": "Compliance Lookup", "is_side_effecting": False, "inputs": {"domain": "str"}, "outputs": {"compliance_mandates": "List[Fact]"}},
    {"key": "market_research", "kind": "acquisition", "name": "Market Research", "is_side_effecting": False, "inputs": {"topic": "str"}, "outputs": {"market_facts": "List[Fact]"}},
    {"key": "derive_requirements", "kind": "derivation", "name": "Derive Structured Requirements", "is_side_effecting": True, "inputs": {"raw_facts": "List[Fact]"}, "outputs": {"Requirement": "structured_requirement"}},
    {"key": "model_process", "kind": "derivation", "name": "Model Process Steps", "is_side_effecting": True, "inputs": {"Requirement": "structured_requirement"}, "outputs": {"ProcessStep": "process_step"}},
    {"key": "derive_edge_cases", "kind": "derivation", "name": "Derive Edge Cases", "is_side_effecting": True, "inputs": {"Requirement": "structured_requirement"}, "outputs": {"Risk": "edge_case_risk"}},
    {"key": "derive_nfr", "kind": "derivation", "name": "Derive Non-Functional Requirements", "is_side_effecting": True, "inputs": {"Requirement": "structured_requirement"}, "outputs": {"NFR": "non_functional_requirement"}},
    {"key": "derive_data_model", "kind": "derivation", "name": "Derive Data Model", "is_side_effecting": True, "inputs": {"Requirement": "structured_requirement"}, "outputs": {"Entity": "data_model_entity"}},
    {"key": "derive_stakeholders", "kind": "derivation", "name": "Derive Stakeholders", "is_side_effecting": True, "inputs": {"raw_facts": "List[Fact]"}, "outputs": {"Stakeholder": "stakeholder_register"}},
    {"key": "derive_raci", "kind": "derivation", "name": "Derive RACI Matrix", "is_side_effecting": True, "inputs": {"ProcessStep": "process_step"}, "outputs": {"RACI": "raci_assignment"}},
    {"key": "derive_risks", "kind": "derivation", "name": "Derive Risk Log", "is_side_effecting": True, "inputs": {"raw_facts": "List[Fact]"}, "outputs": {"Risk": "risk_log_entry"}},
    {"key": "derive_glossary_terms", "kind": "derivation", "name": "Derive Glossary Terms", "is_side_effecting": True, "inputs": {"raw_facts": "List[Fact]"}, "outputs": {"GlossaryTerm": "glossary_definition"}},
    {"key": "derive_options", "kind": "derivation", "name": "Derive Solution Options", "is_side_effecting": True, "inputs": {"Requirement": "structured_requirement"}, "outputs": {"SolutionOption": "option_tradeoff"}},
]


async def seed_standard_capabilities(session: AsyncSession, org_id: Optional[str] = None) -> List[BaCapability]:
    """Seeds all 14 standard acquisition and derivation capabilities into the catalog for planner discovery."""
    seeded = []
    for cap_def in STANDARD_CAPABILITIES:
        existing = await get_capability(session, cap_def["key"], org_id=org_id)
        if not existing:
            cap = await register_capability(
                session,
                key=cap_def["key"],
                kind=cap_def["kind"],
                name=cap_def["name"],
                org_id=org_id,
                is_side_effecting=cap_def["is_side_effecting"],
                inputs=cap_def["inputs"],
                outputs=cap_def["outputs"],
            )
            seeded.append(cap)
        else:
            seeded.append(existing)
    return seeded


# ---------------------------------------------------------------------------
# Industry Template registry (same shape as capability functions above)
# ---------------------------------------------------------------------------

async def register_industry_template(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    org_id: Optional[str] = None,
    description: Optional[str] = None,
    default_instructions: Optional[str] = None,
    default_must_have: Optional[str] = None,
    default_should_have: Optional[str] = None,
    ontology_type_keys: Optional[List[str]] = None,
    capability_keys: Optional[List[str]] = None,
) -> BaIndustryTemplate:
    """Registers an industry template. org_id=None registers a global template."""
    tpl = BaIndustryTemplate(
        id=str(uuid.uuid4()),
        key=key,
        org_id=org_id,
        name=name,
        description=description,
        default_instructions=default_instructions,
        default_must_have=default_must_have,
        default_should_have=default_should_have,
        ontology_type_keys=ontology_type_keys,
        capability_keys=capability_keys,
    )
    session.add(tpl)
    await session.flush()
    return tpl


async def get_industry_template(
    session: AsyncSession,
    key: str,
    org_id: Optional[str] = None,
) -> Optional[BaIndustryTemplate]:
    """Gets an industry template by key. Tenant row overrides global when org_id provided."""
    if org_id is not None:
        stmt = (
            select(BaIndustryTemplate)
            .where(BaIndustryTemplate.key == key)
            .where(or_(BaIndustryTemplate.org_id == org_id, BaIndustryTemplate.org_id.is_(None)))
            .order_by(BaIndustryTemplate.org_id.nulls_last())
        )
    else:
        stmt = select(BaIndustryTemplate).where(
            BaIndustryTemplate.key == key, BaIndustryTemplate.org_id.is_(None)
        )
    res = await session.execute(stmt)
    return res.scalars().first()


async def list_industry_templates(
    session: AsyncSession,
    org_id: Optional[str] = None,
) -> List[BaIndustryTemplate]:
    """Lists all industry templates, resolving tenant overrides for org_id."""
    stmt = (
        select(BaIndustryTemplate)
        .where(or_(BaIndustryTemplate.org_id == org_id, BaIndustryTemplate.org_id.is_(None)))
        .order_by(BaIndustryTemplate.org_id.nulls_last())
    ) if org_id is not None else select(BaIndustryTemplate).where(BaIndustryTemplate.org_id.is_(None))

    res = await session.execute(stmt)
    all_tpls = res.scalars().all()

    resolved: Dict[str, BaIndustryTemplate] = {}
    for tpl in all_tpls:
        if tpl.key not in resolved or tpl.org_id is not None:
            resolved[tpl.key] = tpl
    return list(resolved.values())


# Six standard templates — flat defaults, no DSL, no versioning (YAGNI)
STANDARD_INDUSTRY_TEMPLATES: List[Dict[str, Any]] = [
    {
        "key": "erp",
        "name": "Enterprise Resource Planning",
        "description": "ERP system covering finance, procurement, inventory, HR, and reporting.",
        "default_instructions": "Focus on module integration, master data management, and approval workflows.",
        "default_must_have": "Core financial ledger, procurement lifecycle, inventory management, role-based access.",
        "default_should_have": "Multi-currency support, audit trails, configurable approval hierarchies.",
        "ontology_type_keys": ["Requirement", "Entity", "ProcessStep", "BusinessRule"],
        "capability_keys": ["derive_requirements", "derive_data_model", "model_process", "derive_raci"],
    },
    {
        "key": "crm",
        "name": "Customer Relationship Management",
        "description": "CRM covering lead management, opportunity tracking, customer 360, and sales analytics.",
        "default_instructions": "Prioritise sales pipeline visibility, contact data quality, and integration with marketing tools.",
        "default_must_have": "Contact and account management, opportunity lifecycle, activity logging.",
        "default_should_have": "Email integration, pipeline dashboards, territory management.",
        "ontology_type_keys": ["Requirement", "Stakeholder", "Entity", "ProcessStep"],
        "capability_keys": ["derive_requirements", "derive_stakeholders", "model_process"],
    },
    {
        "key": "hrms",
        "name": "Human Resource Management System",
        "description": "HRMS covering employee lifecycle, payroll, leave, performance, and compliance.",
        "default_instructions": "Emphasise data privacy (GDPR/local labour law), payroll accuracy, and self-service portals.",
        "default_must_have": "Employee master, payroll engine, leave management, onboarding/offboarding workflows.",
        "default_should_have": "Performance review cycles, learning management, org-chart visualisation.",
        "ontology_type_keys": ["Requirement", "BusinessRule", "Entity", "ProcessStep"],
        "capability_keys": ["derive_requirements", "derive_data_model", "model_process", "derive_nfr"],
    },
    {
        "key": "healthcare",
        "name": "Healthcare Information System",
        "description": "HIS covering patient registration, clinical workflows, billing, and regulatory compliance.",
        "default_instructions": "HL7/FHIR interoperability, HIPAA/local health-data regulation, and clinical decision support are non-negotiable.",
        "default_must_have": "Patient demographics, appointment scheduling, clinical notes, billing integration.",
        "default_should_have": "Lab order management, pharmacy module, patient portal.",
        "ontology_type_keys": ["Requirement", "BusinessRule", "NFR", "Risk"],
        "capability_keys": ["derive_requirements", "derive_nfr", "derive_risks", "compliance_lookup"],
    },
    {
        "key": "banking",
        "name": "Banking & Financial Services",
        "description": "Core banking covering accounts, transactions, lending, KYC, and regulatory reporting.",
        "default_instructions": "Regulatory compliance (AML, KYC, Basel III) and zero-downtime are the primary constraints.",
        "default_must_have": "Account management, transaction processing, KYC/AML workflow, audit logging.",
        "default_should_have": "Lending origination, interest calculation engine, regulatory report generation.",
        "ontology_type_keys": ["Requirement", "BusinessRule", "NFR", "Risk", "Entity"],
        "capability_keys": ["derive_requirements", "derive_nfr", "derive_risks", "compliance_lookup", "derive_data_model"],
    },
    {
        "key": "education",
        "name": "Education Management System",
        "description": "EMS covering student lifecycle, curriculum, assessment, and institutional reporting.",
        "default_instructions": "Focus on student data privacy (FERPA/COPPA), accessibility (WCAG 2.1 AA), and multi-role portals.",
        "default_must_have": "Student enrolment, course catalogue, grading, attendance tracking.",
        "default_should_have": "Learning outcome mapping, parent portal, institutional analytics.",
        "ontology_type_keys": ["Requirement", "Stakeholder", "Entity", "ProcessStep"],
        "capability_keys": ["derive_requirements", "derive_stakeholders", "model_process", "derive_data_model"],
    },
]


async def seed_standard_industry_templates(
    session: AsyncSession,
    org_id: Optional[str] = None,
) -> List[BaIndustryTemplate]:
    """Seeds the 6 standard industry templates. Idempotent — skips any key that already exists."""
    seeded = []
    for tpl_def in STANDARD_INDUSTRY_TEMPLATES:
        existing = await get_industry_template(session, tpl_def["key"], org_id=org_id)
        if not existing:
            tpl = await register_industry_template(
                session,
                key=tpl_def["key"],
                name=tpl_def["name"],
                org_id=org_id,
                description=tpl_def.get("description"),
                default_instructions=tpl_def.get("default_instructions"),
                default_must_have=tpl_def.get("default_must_have"),
                default_should_have=tpl_def.get("default_should_have"),
                ontology_type_keys=tpl_def.get("ontology_type_keys"),
                capability_keys=tpl_def.get("capability_keys"),
            )
            seeded.append(tpl)
        else:
            seeded.append(existing)
    return seeded


async def seed_all_ba_catalogs(session: AsyncSession) -> None:
    """Seeds capabilities, ontology types, deliverable specs, and industry templates."""
    await seed_standard_capabilities(session)
    await seed_deliverable_specs(session)
    await seed_standard_industry_templates(session)

