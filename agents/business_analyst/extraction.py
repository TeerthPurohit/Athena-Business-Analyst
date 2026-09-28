"""Chat/document text -> BaFact extraction (§ chat/document fact extraction design).

See docs/superpowers/specs/2026-08-05-ba-chat-fact-extraction-design.md for the field mapping and
docs/superpowers/specs/2026-09-25-ba-concurrent-ingestion-design.md for the analyze/persist split:
analyze_* runs the LLM and the finding judge without touching the DB, so many chunks can be
analyzed concurrently; persist_* writes the accepted findings through one AsyncSession, in order.
"""
import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.facts import BATenantContext, assert_fact
from agents.business_analyst.business_context import (
    ENTITY_KEY_FIELDS,
    empty_business_context,
    is_empty,
    normalize_business_context,
    record_key,
)
from agents.business_analyst.ir import ProjectIR, ProjectScope
from agents.business_analyst.jev_client import judge_findings_validity
from agents.business_analyst.observability import traced
from agents.business_analyst.requirements import (
    ExtractedRequirement,
    RequirementExtraction,
    SourceSpan,
)
from agents.business_analyst.semantic_planner import (
    build_project_ir_llm,
    build_requirement_extraction_llm,
)

PROJECT_PREDICATES = ("in_scope", "out_of_scope", "constraint", "decision", "business_rule", "assumption", "open_question")


async def _judge_in_batches(findings: list[dict[str, Any]], evidence: str) -> dict[str, bool]:
    batches = [findings[offset:offset + 10] for offset in range(0, len(findings), 10)]
    judgments: dict[str, bool] = {}
    for result in await asyncio.gather(*(judge_findings_validity(batch, evidence=evidence) for batch in batches)):
        judgments.update(result)
    return judgments


def _project_items(ir: ProjectIR) -> dict[str, list[str]]:
    return {
        "in_scope": ir.scope.in_scope,
        "out_of_scope": ir.scope.out_of_scope,
        "constraint": ir.scope.constraints,
        "decision": ir.decisions,
        "business_rule": ir.business_rules,
        "assumption": ir.assumptions,
        "open_question": ir.open_questions,
    }


def _known(record: dict[str, Any]) -> dict[str, Any]:
    return {field: value for field, value in record.items() if not is_empty(value)}


@traced("extract-project-context", input=lambda args: args["text"], output=lambda ir: ir.model_dump(exclude_defaults=True))
async def analyze_facts(text: str) -> ProjectIR:
    """Parses `text` into a ProjectIR and keeps only findings the judge accepts. No DB access."""
    ir: ProjectIR = await build_project_ir_llm(text)
    items = _project_items(ir)
    context = normalize_business_context(ir.business_context)

    findings: list[dict[str, Any]] = []
    for idx, obj in enumerate(ir.objectives):
        findings.append({
            "id": f"objective_{idx}",
            "claim": f"Objective: {obj.description} (Category: {obj.category}, Priority: {obj.priority})",
            "details": {"id": obj.id, "category": obj.category, "priority": obj.priority},
        })
    for idx, entity in enumerate(ir.entities):
        findings.append({
            "id": f"entity_{idx}",
            "claim": f"Entity: {entity.name} (type: {entity.type}, attributes: {entity.attributes})",
            "details": {"name": entity.name, "type": entity.type},
        })
    for idx, goal in enumerate(ir.goals):
        findings.append({
            "id": f"goal_{idx}",
            "claim": f"Goal: {goal.name} - {goal.description} (metrics: {goal.target_metrics})",
            "details": {"id": goal.id, "name": goal.name},
        })
    for predicate, values in items.items():
        for item_idx, item in enumerate(values):
            findings.append({
                "id": f"scope_{predicate}_{item_idx}",
                "claim": f"{predicate}: {item}",
                "details": {"predicate": predicate, "item": item},
            })
    for section, value in context.items():
        if section in ENTITY_KEY_FIELDS:
            for idx, record in enumerate(value):
                known = _known(record)
                findings.append({
                    "id": f"context:{section}:{idx}",
                    "claim": f"Business context {section} record: {json.dumps(known, ensure_ascii=False, default=str)}",
                    "details": known,
                })
        else:
            for field, field_value in value.items():
                if not is_empty(field_value):
                    findings.append({
                        "id": f"context:{section}:{field}",
                        "claim": f"Business context {section}.{field}: {field_value}",
                        "details": {"section": section, "field": field, "value": field_value},
                    })

    judgments = await _judge_in_batches(findings, evidence=text) if findings else {}

    def accepted(finding_id: str) -> bool:
        return judgments.get(finding_id) is True

    accepted_context = empty_business_context()
    for section, value in context.items():
        if section in ENTITY_KEY_FIELDS:
            accepted_context[section] = [_known(record) for idx, record in enumerate(value) if accepted(f"context:{section}:{idx}")]
        else:
            for field, field_value in value.items():
                if not is_empty(field_value) and accepted(f"context:{section}:{field}"):
                    accepted_context[section][field] = field_value
    accepted_items = {
        predicate: [item for idx, item in enumerate(values) if accepted(f"scope_{predicate}_{idx}")]
        for predicate, values in items.items()
    }
    return ProjectIR(
        project_name=ir.project_name,
        objectives=[obj for idx, obj in enumerate(ir.objectives) if accepted(f"objective_{idx}")],
        entities=[entity for idx, entity in enumerate(ir.entities) if accepted(f"entity_{idx}")],
        goals=[goal for idx, goal in enumerate(ir.goals) if accepted(f"goal_{idx}")],
        scope=ProjectScope(
            in_scope=accepted_items["in_scope"],
            out_of_scope=accepted_items["out_of_scope"],
            constraints=accepted_items["constraint"],
        ),
        decisions=accepted_items["decision"],
        business_rules=accepted_items["business_rule"],
        assumptions=accepted_items["assumption"],
        open_questions=accepted_items["open_question"],
        business_context=accepted_context,
    )


async def persist_facts(
    ctx: BATenantContext,
    session: AsyncSession,
    ir: ProjectIR,
    *,
    source_id: str,
    asserted_by: str,
) -> Dict[str, Any]:
    """Writes an already-judged IR (from analyze_facts) as append-only BaFacts."""
    goal_facts = entity_facts = project_facts = context_facts = 0

    for obj in ir.objectives:
        await assert_fact(
            ctx, session,
            subject_type="goal", subject_key=obj.id, predicate="objective",
            source_id=source_id, asserted_by=asserted_by,
            value={"description": obj.description, "category": obj.category, "priority": obj.priority},
        )
        goal_facts += 1

    for entity in ir.entities:
        await assert_fact(
            ctx, session,
            subject_type="entity", subject_key=entity.name, predicate="described_as",
            source_id=source_id, asserted_by=asserted_by,
            value={"type": entity.type, "attributes": entity.attributes},
        )
        entity_facts += 1

    for goal in ir.goals:
        await assert_fact(
            ctx, session,
            subject_type="goal", subject_key=goal.id, predicate="target_metrics",
            source_id=source_id, asserted_by=asserted_by,
            value={"name": goal.name, "description": goal.description, "target_metrics": goal.target_metrics},
        )
        goal_facts += 1

    for section, value in normalize_business_context(ir.business_context).items():
        if section in ENTITY_KEY_FIELDS:
            for record in value:
                known = _known(record)
                key = record_key(section, known) or hashlib.sha256(
                    json.dumps(known, sort_keys=True, default=str).encode()
                ).hexdigest()[:16]
                await assert_fact(
                    ctx, session,
                    subject_type="business_context", subject_key=f"{section}:{key}", predicate="identified_as",
                    source_id=source_id, asserted_by=asserted_by,
                    value={"section": section, "record": known},
                )
                context_facts += 1
        else:
            for field, field_value in value.items():
                if is_empty(field_value):
                    continue
                await assert_fact(
                    ctx, session,
                    subject_type="business_context", subject_key=f"{section}.{field}", predicate="identified_as",
                    source_id=source_id, asserted_by=asserted_by,
                    value={"section": section, "field": field, "value": field_value},
                )
                context_facts += 1

    for predicate, values in _project_items(ir).items():
        if values:
            await assert_fact(
                ctx, session,
                subject_type="project", subject_key=ctx.project_id, predicate=predicate,
                source_id=source_id, asserted_by=asserted_by,
                value={"items": values},
            )
            project_facts += 1

    return {
        "facts_created": goal_facts + entity_facts + project_facts + context_facts,
        "goal_facts": goal_facts,
        "entity_facts": entity_facts,
        "context_facts": context_facts,
        "ir": ir,
    }


async def extract_and_persist_facts(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    text: str,
    source_id: str,
    asserted_by: str,
) -> Dict[str, Any]:
    """Parses `text` into a judged ProjectIR, then persists it as BaFacts.

    Returns counts plus the filtered IR so callers can render/report without a second round-trip.
    """
    ir = await analyze_facts(text)
    return await persist_facts(ctx, session, ir, source_id=source_id, asserted_by=asserted_by)


def segment_source_text(text: str, *, namespace: str = "", start_offset: int = 0) -> list[SourceSpan]:
    """Returns stable, non-empty line spans with offsets into the original source."""
    spans: list[SourceSpan] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        normalized = raw.strip()
        start = start_offset + cursor + len(raw) - len(raw.lstrip())
        cursor += len(line)
        if not normalized:
            continue
        digest_input = f"{namespace}:{len(spans)}:{start}:{normalized}" if namespace else f"{len(spans)}:{start}:{normalized}"
        digest = hashlib.sha256(digest_input.encode()).hexdigest()[:16]
        spans.append(
            SourceSpan(
                id=f"span-{digest}",
                ordinal=len(spans),
                start_char=start,
                end_char=start + len(normalized),
                text=normalized,
            )
        )
    return spans


def _extraction_prompt_text(spans: list[SourceSpan]) -> str:
    return "\n".join(f"[{span.id}] {span.text}" for span in spans)


@dataclass
class RequirementAnalysis:
    namespace: str
    spans: list[SourceSpan]
    requirements: list[ExtractedRequirement]


@traced(
    "extract-requirements", input=lambda args: args["text"],
    output=lambda analysis: [req.model_dump(exclude_defaults=True) for req in analysis.requirements],
)
async def analyze_requirements(
    text: str,
    *,
    chunk_index: int | None = None,
    start_offset: int = 0,
) -> RequirementAnalysis:
    """Extracts cited requirements and keeps only those the judge accepts. No DB access."""
    namespace = f"chunk-{chunk_index}" if chunk_index is not None else ""
    spans = segment_source_text(text, namespace=namespace, start_offset=start_offset)
    extraction: RequirementExtraction = await build_requirement_extraction_llm(
        _extraction_prompt_text(spans)
    )
    span_by_id = {span.id: span for span in spans}

    # A requirement citing a span that doesn't exist is unverifiable: drop it, keep the rest.
    cited = [req for req in extraction.requirements if set(req.evidence_span_ids) <= span_by_id.keys()]
    if not cited:
        return RequirementAnalysis(namespace, spans, [])

    findings_payload = [{
        "id": req.external_key,
        "claim": f"Requirement: {req.task or req.object or req.category.value}",
        "cited_evidence": [span_by_id[sid].text for sid in req.evidence_span_ids],
        "details": req.model_dump(exclude={"external_key", "evidence_span_ids", "ambiguities"}),
    } for req in cited]
    judgments = await _judge_in_batches(findings_payload, evidence=_extraction_prompt_text(spans))
    return RequirementAnalysis(namespace, spans, [req for req in cited if judgments.get(req.external_key) is True])


async def persist_requirements(
    ctx: BATenantContext,
    session: AsyncSession,
    analysis: RequirementAnalysis,
    *,
    source_id: str,
    asserted_by: str,
) -> Dict[str, Any]:
    """Persists accepted requirements, their cited spans, and explicit gaps."""
    namespace = analysis.namespace
    accepted_span_ids = {span_id for req in analysis.requirements for span_id in req.evidence_span_ids}
    spans_to_persist = [span for span in analysis.spans if span.id in accepted_span_ids]

    for span in spans_to_persist:
        await assert_fact(
            ctx,
            session,
            subject_type="SourceSpan",
            subject_key=span.id,
            predicate="evidence",
            source_id=source_id,
            asserted_by=asserted_by,
            value=span.model_dump(),
        )

    requirements_created = 0
    gaps_created = 0
    for requirement in analysis.requirements:
        requirement_key = f"{source_id}:{namespace}:{requirement.external_key}" if namespace else f"{source_id}:{requirement.external_key}"
        await assert_fact(
            ctx,
            session,
            subject_type="Requirement",
            subject_key=requirement_key,
            predicate="specified_as",
            source_id=source_id,
            asserted_by=asserted_by,
            value=requirement.model_dump(mode="json"),
        )
        requirements_created += 1
        for span_id in requirement.evidence_span_ids:
            await assert_fact(
                ctx,
                session,
                subject_type="Requirement",
                subject_key=requirement_key,
                predicate="derived_from",
                source_id=source_id,
                asserted_by=asserted_by,
                object_type="SourceSpan",
                object_key=span_id,
            )
        for field in requirement.missing_fields():
            await assert_fact(
                ctx,
                session,
                subject_type="Gap",
                subject_key=f"{requirement_key}:{field}",
                predicate="missing_information",
                source_id=source_id,
                asserted_by=asserted_by,
                value={
                    "requirement_key": requirement_key,
                    "field": field,
                    "reason": f"Source evidence does not specify {field}.",
                },
            )
            gaps_created += 1
        for ambiguity in requirement.ambiguities:
            await assert_fact(
                ctx,
                session,
                subject_type="Gap",
                subject_key=f"{requirement_key}:{ambiguity.field}",
                predicate="ambiguous_information",
                source_id=source_id,
                asserted_by=asserted_by,
                value={"requirement_key": requirement_key, **ambiguity.model_dump()},
            )
            gaps_created += 1

    return {
        "source_spans_created": len(spans_to_persist),
        "requirements_created": requirements_created,
        "gaps_created": gaps_created,
    }


async def extract_and_persist_requirements(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    text: str,
    source_id: str,
    asserted_by: str,
    chunk_index: int | None = None,
    start_offset: int = 0,
) -> Dict[str, Any]:
    """Persists cited requirements and explicit gaps from one source transaction."""
    analysis = await analyze_requirements(text, chunk_index=chunk_index, start_offset=start_offset)
    return await persist_requirements(ctx, session, analysis, source_id=source_id, asserted_by=asserted_by)
