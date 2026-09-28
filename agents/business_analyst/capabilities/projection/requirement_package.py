"""Pure fact-store projection for Athena's reviewed requirement package."""

from collections import defaultdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.requirements import (
    ExtractedRequirement,
    render_gherkin,
    render_user_story,
)


async def render_requirement_package(
    ctx: BATenantContext,
    session: AsyncSession,
) -> dict[str, list[dict[str, Any]]]:
    """Project evidence-backed requirements without model calls or fact writes."""
    facts = await get_facts(ctx, session)
    requirements: dict[str, ExtractedRequirement] = {}
    requirement_fact_ids: dict[str, str] = {}
    source_spans: dict[str, dict[str, Any]] = {}
    traces: dict[str, list[str]] = defaultdict(list)
    gaps: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for fact in facts:
        if fact.subject_type == "SourceSpan" and fact.predicate == "evidence" and fact.value:
            source_spans[fact.subject_key] = fact.value
        elif fact.subject_type == "Requirement" and fact.predicate == "specified_as" and fact.value:
            requirements[fact.subject_key] = ExtractedRequirement.model_validate(fact.value)
            requirement_fact_ids[fact.subject_key] = fact.id
        elif fact.subject_type == "Requirement" and fact.predicate == "derived_from" and fact.object_key:
            traces[fact.subject_key].append(fact.object_key)
        elif fact.subject_type == "Gap" and fact.value and fact.value.get("requirement_key"):
            gaps[fact.value["requirement_key"]].append(
                {"gap_key": fact.subject_key, **fact.value}
            )

    projected_requirements: list[dict[str, Any]] = []
    agenda: list[dict[str, Any]] = []
    traceability: list[dict[str, Any]] = []
    for key in sorted(requirements):
        requirement = requirements[key]
        requirement_gaps = sorted(gaps[key], key=lambda gap: (gap.get("field", ""), gap["gap_key"]))
        story = render_user_story(requirement)
        scenario = render_gherkin(requirement)
        projected_requirements.append(
            {
                "requirement_key": key,
                "category": requirement.category.value,
                "requirement": requirement.model_dump(mode="json"),
                "user_story": story,
                "gherkin": scenario,
                "gaps": requirement_gaps,
            }
        )
        for gap in requirement_gaps:
            agenda.append(
                {
                    "requirement_key": key,
                    "category": requirement.category.value,
                    "stakeholder": requirement.stakeholder,
                    "field": gap.get("field", ""),
                    "reason": gap.get("reason", ""),
                    "question": gap.get("question"),
                    "gap_key": gap["gap_key"],
                }
            )
        traceability.append(
            {
                "requirement_key": key,
                "requirement_fact_id": requirement_fact_ids[key],
                "source_span_ids": sorted(set(traces[key])),
                "source_spans": [source_spans[span_id] for span_id in sorted(set(traces[key])) if span_id in source_spans],
                "user_story": story,
                "gherkin": scenario,
                "gap_keys": [gap["gap_key"] for gap in requirement_gaps],
            }
        )

    return {
        "requirements": projected_requirements,
        "agenda": sorted(agenda, key=lambda item: (item["requirement_key"], item["field"], item["gap_key"])),
        "traceability": traceability,
    }
