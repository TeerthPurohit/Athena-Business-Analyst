"""Compact traceability matrix from requirement to criteria, test, and evidence."""

from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import plain, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec


def _items(value):
    values = value if isinstance(value, (list, tuple)) else [value]
    return list(dict.fromkeys(text for item in values if (text := plain(item))))


def _type(value):
    return str(value.get("requirement_type") or value.get("category") or "").lower().replace("-", "_")


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str:
    facts = await get_facts(ctx, session)
    reqs = requirements(facts)
    evidence_by_requirement = defaultdict(list)
    evidence = {
        fact.subject_key: plain((fact.value or {}).get("text"))
        for fact in facts
        if fact.subject_type == "SourceSpan" and fact.predicate == "evidence" and isinstance(fact.value, dict)
    }
    for fact in facts:
        if fact.subject_type == "Requirement" and fact.predicate == "derived_from" and fact.object_type == "SourceSpan":
            evidence_by_requirement[fact.subject_key].append(fact.object_key)

    prefix_by_type = {
        "functional": "FR", "nonfunctional": "NFR", "business_rule": "BRULE",
        "constraint": "CST", "assumption": "ASM", "transitional": "TR",
    }
    counters = defaultdict(int)
    matrix_rows = []
    gaps = defaultdict(list)
    for subject_key, requirement_id, value in reqs:
        kind = _type(value)
        prefix = prefix_by_type.get(kind)
        functional_id = ""
        if prefix:
            counters[kind] += 1
            functional_id = f"{prefix}-{counters[kind]:03d}"

        explicit = _items(value.get("acceptance_criteria"))
        if explicit:
            acceptance_ids = [f"AC-{requirement_id}-{index:02d}" for index in range(1, len(explicit) + 1)]
            test_ids = [f"TC-{requirement_id}-{index:02d}" for index in range(1, len(explicit) + 1)]
        elif plain(value.get("trigger")) and _items(value.get("outcomes")):
            acceptance_ids = [f"AC-{requirement_id}-01"]
            test_ids = [f"TC-{requirement_id}"]
        else:
            acceptance_ids = []
            test_ids = []

        span_ids = list(dict.fromkeys(
            [str(span_id) for span_id in value.get("evidence_span_ids", []) if span_id]
            + evidence_by_requirement.get(subject_key, [])
        ))
        trace_excerpt = "; ".join(
            f"{span_id}: {evidence[span_id][:140]}{'...' if len(evidence[span_id]) > 140 else ''}"
            for span_id in span_ids if evidence.get(span_id)
        )
        status = "Draft - review required"
        if not acceptance_ids:
            gaps["Acceptance criteria"].append(requirement_id)
            status = "Draft - needs clarification"
        if not span_ids:
            gaps["Source evidence"].append(requirement_id)
            status = "Draft - needs clarification"

        matrix_rows.append((
            f"**{requirement_id}**" + (f"<br>{functional_id}" if functional_id else ""),
            f"**Acceptance:** {', '.join(acceptance_ids) or 'Needs clarification'}<br>"
            f"**Test:** {', '.join(test_ids) or 'Needs test design'}",
            f"**Evidence spans:** {', '.join(span_ids) or 'Needs source trace'}<br>"
            f"**Status:** {status}",
        ))
        if trace_excerpt:
            matrix_rows[-1] += (trace_excerpt,)

    md = "# Requirements Traceability Matrix (RTM)\n\n"
    md += "This matrix links source requirement IDs to functional IDs, acceptance criteria, test cases, and evidence spans. Business requirement and objective links are maintained in the Requirements Register workbook.\n\n"
    md += f"{len(matrix_rows)} current source requirements are represented. All items remain drafts until their links and verification evidence are reviewed.\n\n"
    if matrix_rows:
        md += "| Requirement IDs | Verification Links | Source Links and Status | Evidence Excerpt |\n"
        md += "| --- | --- | --- | --- |\n"
        for row in matrix_rows:
            if len(row) == 3:
                row += ("",)
            md += "| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", "<br>") for cell in row) + " |\n"
    else:
        md += "No current requirement records are available for traceability.\n"

    if gaps:
        md += "\n## Traceability Gaps\n\n"
        for gap, requirement_ids in gaps.items():
            md += f"**{gap} missing ({len(requirement_ids)}):** " + ", ".join(requirement_ids) + "\n\n"
    md += "Implementation evidence is not yet recorded in the project requirement facts. Link verified tests and implementation references before treating coverage as complete.\n"
    return md
