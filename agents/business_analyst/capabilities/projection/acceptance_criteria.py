"""Acceptance Criteria Renderer. Structural, zero LLM calls."""

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import REQUIREMENTS_NEEDED, plain, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Acceptance Criteria Specification"
NEEDS = REQUIREMENTS_NEEDED


def _items(value) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    return list(dict.fromkeys(text for item in values if (text := plain(item))))


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    reqs = requirements(await get_facts(ctx, session))
    if not reqs:
        return None

    md = f"# {TITLE}\n\n"
    recorded = []
    missing = []
    for _, requirement_id, value in reqs:
        explicit = _items(value.get("acceptance_criteria"))
        preconditions = _items(value.get("preconditions"))
        trigger = plain(value.get("trigger"))
        outcomes = _items(value.get("outcomes"))
        if explicit:
            for index, criterion in enumerate(explicit, 1):
                recorded.append((f"AC-{requirement_id}-{index:02d}", requirement_id, criterion, "Source-authored"))
        elif trigger and outcomes:
            clauses = []
            if preconditions:
                clauses.append("**Given** " + "; ".join(preconditions))
            clauses.append("**When** " + trigger)
            clauses.append("**Then** " + "; ".join(outcomes))
            recorded.append((
                f"AC-{requirement_id}-01",
                requirement_id,
                "  \n".join(clauses),
                "Derived from recorded conditions and outcomes",
            ))
        else:
            missing.append(requirement_id)

    md += (
        "Only source-backed criteria are shown. Criteria derived from project facts use the recorded trigger "
        "and expected outcomes; missing behavior is left for stakeholder review.\n\n"
    )
    for criterion_id, requirement_id, criterion, origin in recorded:
        md += f"### {criterion_id} - {requirement_id}\n\n{criterion}\n\n**Basis:** {origin}\n\n"
    if missing:
        md += f"## Requirements needing acceptance decisions ({len(missing)})\n\n"
        md += "No explicit criterion or complete trigger-and-outcome pair is available for these source requirements:\n\n"
        md += "".join(f"- {requirement_id}\n" for requirement_id in missing)
    return md
