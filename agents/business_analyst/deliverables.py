"""Deliverable management, staleness computation, approval workflow, and catalog seeding (§11.1 - §11.5).

CLAUDE.md Non-Negotiables:
- staleness computation: lazy-on-read max(ba_fact.seq) over input node types > frontier_seq.
- deliverable instance approval: distinct verb, using set_approval_context(True).
- seed_deliverable_specs(): seeds all 17 deliverable specs and renderers into catalog.
"""
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.api.security import set_approval_context
from agents.business_analyst.facts import BATenantContext, assert_fact
from agents.business_analyst.models import (
    BaDeliverableInstance,
    BaDeliverableSpec,
    BaFact,
    BaProject,
    BaRenderer,
)

DELIVERABLE_CATALOG_SEEDS = [
    {
        "key": "brd",
        "purpose": "Business Requirements Document summarizing executive narrative, scope, and high-level requirements.",
        "required_node_types": ["Requirement", "business_context"],
        "required_capabilities": ["derive_requirements"],
        "renderer_key": "brd",
        "review_process": "stakeholder_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "narrated",
        "prompt_id": "ba_brd_narrative_v2",
    },
    {
        "key": "frd",
        "purpose": "Functional Requirements Document detailing functional specifications and system capabilities.",
        "required_node_types": ["Requirement"],
        "required_capabilities": ["derive_requirements", "model_process"],
        "renderer_key": "frd",
        "review_process": "stakeholder_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "nfr_spec",
        "purpose": "Non-Functional Requirements Specification detailing performance, security, and scalability targets.",
        "required_node_types": ["Requirement"],
        "required_capabilities": ["derive_nfr"],
        "renderer_key": "nfr_spec",
        "review_process": "self_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "user_story",
        "purpose": "Agile User Stories formatted with actor, action, benefit, and acceptance criteria pointers.",
        "required_node_types": ["Requirement"],
        "required_capabilities": ["derive_requirements"],
        "renderer_key": "user_story",
        "review_process": "self_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "none",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "use_case",
        "purpose": "Use Case Specifications mapping system triggers, main flows, and alternate paths.",
        "required_node_types": ["business_context"],
        "required_capabilities": ["model_process", "derive_edge_cases"],
        "renderer_key": "use_case",
        "review_process": "stakeholder_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "acceptance_criteria",
        "purpose": "Given-When-Then Acceptance Criteria for requirements validation.",
        "required_node_types": ["Requirement"],
        "required_capabilities": ["derive_requirements"],
        "renderer_key": "acceptance_criteria",
        "review_process": "self_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "test_case",
        "purpose": "Test Case Suite generated from functional specifications and acceptance criteria.",
        "required_node_types": ["Requirement"],
        "required_capabilities": ["derive_requirements"],
        "renderer_key": "test_case",
        "review_process": "self_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "process_model",
        "purpose": "Process Model representation mapping activity steps, actors, and decision nodes.",
        "required_node_types": ["business_context"],
        "required_capabilities": ["model_process"],
        "renderer_key": "process_model",
        "review_process": "stakeholder_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "data_dictionary",
        "purpose": "Data Dictionary cataloging domain entities, attributes, data types, and relationships.",
        "required_node_types": ["entity"],
        "required_capabilities": ["derive_data_model"],
        "renderer_key": "data_dictionary",
        "review_process": "self_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "none",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "rtm",
        "purpose": "Requirements Traceability Matrix mapping objectives to requirements and test cases.",
        "required_node_types": ["Requirement", "SourceSpan"],
        "required_capabilities": [],  # Free scan over derived_from/traces_to
        "renderer_key": "rtm",
        "review_process": "client_signoff",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "multi_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "stakeholder_register",
        "purpose": "Stakeholder Register detailing project stakeholders, roles, and impact levels.",
        "required_node_types": ["business_context"],
        "required_capabilities": ["derive_stakeholders"],
        "renderer_key": "stakeholder_register",
        "review_process": "stakeholder_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "raci_matrix",
        "purpose": "RACI Matrix mapping Responsible, Accountable, Consulted, and Informed assignments.",
        "required_node_types": ["RACI"],
        "required_capabilities": ["derive_raci"],
        "renderer_key": "raci_matrix",
        "review_process": "stakeholder_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "risk_log",
        "purpose": "Risk Log cataloging identified project risks, severity, probability, and mitigations.",
        "required_node_types": ["business_context"],
        "required_capabilities": ["derive_risks"],
        "renderer_key": "risk_log",
        "review_process": "stakeholder_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "single_approver",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "gap_report",
        "purpose": "Gap Report identifying missing parameters, unspecified thresholds, and unfulfilled requirements.",
        "required_node_types": ["Requirement", "Gap"],
        "required_capabilities": [],  # Free scan over gap facts
        "renderer_key": "gap_report",
        "review_process": "self_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "none",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "glossary",
        "purpose": "Business Glossary defining domain terms, abbreviations, and acronyms.",
        "required_node_types": ["Term", "GlossaryTerm"],
        "required_capabilities": ["derive_glossary_terms"],
        "renderer_key": "glossary",
        "review_process": "self_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "none",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "change_log",
        "purpose": "Change Log auditing fact updates, replacement chains, and version history.",
        "required_node_types": ["Requirement"],
        "required_capabilities": [],  # Free scan over replaces facts
        "renderer_key": "change_log",
        "review_process": "self_review",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "none",
        "narration_mode": "structural",
        "prompt_id": None,
    },
    {
        "key": "options_analysis",
        "purpose": "Options Analysis evaluating architectural and solution trade-offs.",
        "required_node_types": ["Option", "SolutionOption"],
        "required_capabilities": ["derive_options"],
        "renderer_key": "options_analysis",
        "review_process": "client_signoff",
        "versioning_strategy": "seq_snapshot",
        "approval_workflow": "multi_approver",
        "narration_mode": "narrated",
        "prompt_id": "ba_options_narrative_v2",
    },
]


async def seed_deliverable_specs(session: AsyncSession) -> None:
    """Seeds all 17 deliverable specs and renderers into catalog tables if missing."""
    for item in DELIVERABLE_CATALOG_SEEDS:
        key = item["key"]
        # Check spec existence
        stmt_spec = select(BaDeliverableSpec).where(
            BaDeliverableSpec.key == key,
            BaDeliverableSpec.org_id.is_(None),
        )
        res_spec = await session.execute(stmt_spec)
        spec = res_spec.scalar_one_or_none()

        if not spec:
            spec = BaDeliverableSpec(
                id=str(uuid.uuid4()),
                key=key,
                org_id=None,
                purpose=item["purpose"],
                required_node_types=item["required_node_types"],
                required_capabilities=item["required_capabilities"],
                renderer_key=item["renderer_key"],
                review_process=item["review_process"],
                versioning_strategy=item["versioning_strategy"],
                approval_workflow=item["approval_workflow"],
            )
            session.add(spec)
        elif spec.required_node_types != item["required_node_types"]:
            # Keep existing global specs in step with what the renderers read (drives is_stale).
            spec.required_node_types = item["required_node_types"]

        renderer_output_format = "pdf" if key == "brd" else "markdown"
        stmt_rnd = select(BaRenderer).where(
            BaRenderer.key == key,
        )
        res_rnd = await session.execute(stmt_rnd)
        renderers = res_rnd.scalars().all()
        rnd = next(
            (renderer for renderer in renderers if renderer.output_format == renderer_output_format),
            None,
        )

        # Upgrade the old BRD renderer row in place so existing databases advertise PDF too.
        if rnd is None and renderer_output_format == "pdf":
            rnd = next((renderer for renderer in renderers if renderer.output_format == "markdown"), None)
            if rnd is not None:
                rnd.output_format = renderer_output_format

        if not rnd:
            rnd = BaRenderer(
                id=str(uuid.uuid4()),
                key=key,
                output_format=renderer_output_format,
                renderer_fn=f"agents.business_analyst.capabilities.projection.{key}.render",
                renderer_version="1.0.0",
                narration_mode=item["narration_mode"],
                prompt_id=item["prompt_id"],
            )
            session.add(rnd)
        elif rnd.prompt_id != item["prompt_id"]:
            rnd.prompt_id = item["prompt_id"]

    await session.flush()


async def is_stale(instance: BaDeliverableInstance, session: AsyncSession) -> bool:
    """Computes staleness lazily on read.

    is_stale(instance) := max(ba_fact.seq) over input node types for project > instance.frontier_seq
    """
    stmt_spec = select(BaDeliverableSpec).where(
        BaDeliverableSpec.key == instance.deliverable_key,
        (BaDeliverableSpec.org_id.is_(None)) | (BaDeliverableSpec.org_id == instance.org_id),
    )
    res_spec = await session.execute(stmt_spec)
    specs = res_spec.scalars().all()
    spec = next((item for item in specs if item.org_id == instance.org_id), None)
    if spec is None:
        spec = next((item for item in specs if item.org_id is None), None)

    node_types = spec.required_node_types if spec else []
    if not node_types:
        return False

    stmt_max = (
        select(func.max(BaFact.seq))
        .where(
            BaFact.project_id == instance.project_id,
            BaFact.org_id == instance.org_id,
            BaFact.subject_type.in_(node_types),
        )
    )
    res_max = await session.execute(stmt_max)
    max_seq = res_max.scalar() or 0
    if max_seq > instance.frontier_seq:
        return True

    # Business-context deliverables also read project settings, which facts don't version.
    if "business_context" in node_types:
        project = await session.get(BaProject, instance.project_id)
        updated = project.summary_updated_at if project else None
        return bool(updated and instance.created_at and updated > instance.created_at)
    return False


async def approve_deliverable_instance(
    instance_id: str,
    approved_by: str,
    ctx: BATenantContext,
    session: AsyncSession,
) -> BaDeliverableInstance:
    """Approves a deliverable instance using an authorized approval context.

    Direct ORM mutation without this function is blocked by before_flush_privileged_guard.
    """
    stmt = select(BaDeliverableInstance).where(
        BaDeliverableInstance.id == instance_id,
        BaDeliverableInstance.project_id == ctx.project_id,
        BaDeliverableInstance.org_id == ctx.org_id,
    )
    res = await session.execute(stmt)
    instance = res.scalar_one_or_none()

    if not instance:
        raise ValueError(f"Deliverable instance '{instance_id}' not found for tenant context.")

    set_approval_context(True)
    try:
        instance.status = "approved"
        instance.approved_by = approved_by
        instance.approved_at = datetime.now(timezone.utc)
        await session.flush()
    finally:
        set_approval_context(False)

    return instance
