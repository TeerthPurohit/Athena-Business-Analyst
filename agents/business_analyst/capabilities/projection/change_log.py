"""Change Log Renderer. Structural, free scan over replaces chain, ZERO LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import label, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str:
    facts = await get_facts(ctx, session)
    replaced_facts = [f for f in facts if f.replaces is not None]
    req_labels = {key: name for key, name, _ in requirements(facts)}

    md = "# Project Fact Change Log\n\n"
    if not replaced_facts:
        md += "No fact updates or replacement chains recorded for this project."
        return md

    md += "| Item | Type | Change | Recorded By | When |\n| --- | --- | --- | --- | --- |\n"
    for r in replaced_facts:
        change = "Approved" if r.human_approval else "Updated"
        when = r.asserted_at.strftime("%Y-%m-%d %H:%M UTC")
        md += f"| {req_labels.get(r.subject_key, r.subject_key)} | {label(r.subject_type)} | {change} | {label(r.asserted_by.removeprefix('ba_'))} | {when} |\n"

    return md
