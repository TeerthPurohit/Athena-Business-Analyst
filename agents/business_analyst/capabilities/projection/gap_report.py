"""Gap Report Renderer. Structural, free scan over gap facts, ZERO LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import label, plain, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str:
    facts = await get_facts(ctx, session)
    req_labels = {key: name for key, name, _ in requirements(facts)}
    # Latest fact per gap; gaps written by the old capability gate (value has
    # "missing_capabilities") were never true, so they are not reported.
    gaps = {
        (f.subject_key, f.predicate): f for f in facts
        if (f.predicate == "gap" or f.subject_type == "Gap")
        and not (isinstance(f.value, dict) and "missing_capabilities" in f.value)
    }.values()

    md = "# Gap Analysis Report\n\n"
    if not gaps:
        md += "No open gaps or missing parameters identified for this project."
        return md

    md += "| Area | Topic | What is missing |\n| --- | --- | --- |\n"
    for g in gaps:
        val = g.value if isinstance(g.value, dict) else {"reason": plain(g.value)}
        if val.get("requirement_key"):
            area = req_labels.get(val["requirement_key"], "Requirement")
        elif val.get("deliverable_key"):
            area = "Deliverables"
        elif val.get("source_id"):
            area = "Source analysis"
        else:
            area = "Project"
        reason = plain(val.get("reason"))
        reason = reason if " " in reason else label(reason)
        detail = " ".join(filter(None, [reason, plain(val.get("question"))]))
        topic = label(val.get("field")) if val.get("field") else ""
        md += f"| {area} | {topic.replace('|', '/')} | {detail.replace('|', '/')} |\n"

    return md
