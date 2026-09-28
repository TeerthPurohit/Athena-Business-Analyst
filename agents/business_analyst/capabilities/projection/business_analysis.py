"""Evidence-backed grouping of detailed requirements into business requirements."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.llm_client import get_structured_output
from agents.business_analyst.models import BaProject
from models.agent_prompt import fetch_prompt

logger = logging.getLogger(__name__)


class BusinessRequirementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=120)
    statement: str = Field(min_length=10, max_length=700)
    source_requirement_ids: list[str] = Field(default_factory=list)
    functional_areas: list[str] = Field(default_factory=list)
    business_problem: str | None = None
    business_objective: str | None = None
    stakeholder: str | None = None
    business_benefit: str | None = None
    priority: str | None = None
    success_metric: str | None = None
    clarification_questions: list[str] = Field(default_factory=list)


class BusinessAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_summary: str = ""
    business_requirements: list[BusinessRequirementDraft] = Field(default_factory=list, max_length=40)


def numbered_business_requirements(analysis: BusinessAnalysis) -> list[dict[str, Any]]:
    return [
        {"id": f"BR-{index:03d}", **item.model_dump()}
        for index, item in enumerate(analysis.business_requirements, 1)
    ]


def _analysis_input(project_name, summary, context, requirements):
    business_keys = (
        "vision", "mission", "problem_statement", "business_goals", "project_purpose",
        "functional_scope", "in_scope_features", "out_of_scope_features", "important_assumptions",
        "stakeholders", "success_measures", "key_business_rules", "constraints", "dependencies",
        "risks", "open_decisions",
    )
    summary_data = {key: summary.get(key) for key in business_keys if summary.get(key)}
    context_keys = (
        "stakeholders", "users_personas", "business_rules", "processes", "workflows", "risks",
        "constraints", "dependencies", "assumptions", "glossary",
    )
    context_data = {key: context.get(key) for key in context_keys if context.get(key)}
    requirement_data = []
    for _, requirement_id, value in requirements:
        requirement_data.append({
            "id": requirement_id,
            "type": value.get("requirement_type") or value.get("category"),
            "functional_area": value.get("functional_area"),
            "statement": value.get("task"),
            "object": value.get("object"),
            "business_problem": value.get("business_problem"),
            "business_objective": value.get("business_objective"),
            "stakeholder": value.get("stakeholder"),
            "benefit": value.get("benefit"),
            "priority": value.get("priority"),
            "success_metric": value.get("success_metric"),
            "trigger": value.get("trigger"),
            "outcomes": value.get("outcomes"),
            "constraints": value.get("constraints"),
        })
    return {
        "project": project_name,
        "business_summary": summary_data,
        "business_context": context_data,
        "detailed_requirements": requirement_data,
    }


async def get_business_analysis(
    ctx: BATenantContext,
    session: AsyncSession,
    project_name: str,
    summary: dict,
    context: dict,
    requirements: list[tuple[str, str, dict]],
) -> BusinessAnalysis:
    """Returns a cached analysis for the current record, refreshing it when evidence changes."""
    payload = _analysis_input(project_name, summary, context, requirements)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    project = await session.get(BaProject, ctx.project_id)
    if project is None or project.org_id != ctx.org_id:
        return BusinessAnalysis()

    try:
        system_prompt = await fetch_prompt("9", "ba_brd_business_analysis_v1")
    except Exception as exc:  # noqa: BLE001 - keep factual documents available during prompt outages
        logger.warning("BA_BUSINESS_ANALYSIS_DEGRADED project_id=%s reason=%s", ctx.project_id, exc)
        return BusinessAnalysis()
    fingerprint = hashlib.sha256((system_prompt + "\n" + encoded).encode("utf-8")).hexdigest()

    settings = project.settings or {}
    cached = settings.get("business_document_analysis") if isinstance(settings, dict) else None
    if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
        try:
            return BusinessAnalysis.model_validate(cached.get("analysis") or {})
        except Exception:  # stale or pre-release cache shape
            logger.info("BA_BUSINESS_ANALYSIS_CACHE_INVALID project_id=%s", ctx.project_id)

    try:
        analysis = await get_structured_output(
            system_prompt=system_prompt,
            user_prompt=encoded,
            response_model=BusinessAnalysis,
            agent_id="9",
            name="consolidate-business-requirements",
            session_id=ctx.project_id,
        )
        valid_ids = {requirement_id for _, requirement_id, _ in requirements}
        normalized = []
        for item in analysis.business_requirements:
            source_ids = list(dict.fromkeys(
                requirement_id for requirement_id in item.source_requirement_ids
                if requirement_id in valid_ids
            ))
            if source_ids and item.title.strip() and item.statement.strip():
                normalized.append(item.model_copy(update={"source_requirement_ids": source_ids}))
        analysis = analysis.model_copy(update={"business_requirements": normalized[:40]})
    except Exception as exc:  # noqa: BLE001 - keep factual summary/register available during LLM outages
        logger.warning("BA_BUSINESS_ANALYSIS_DEGRADED project_id=%s reason=%s", ctx.project_id, exc)
        return BusinessAnalysis()

    await session.refresh(project, attribute_names=["settings"])
    updated_settings = dict(project.settings or {})
    updated_settings["business_document_analysis"] = {
        "fingerprint": fingerprint,
        "analysis": analysis.model_dump(mode="json"),
    }
    project.settings = updated_settings
    await session.flush()
    return analysis
