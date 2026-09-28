"""RTM Renderer (Requirements Traceability Matrix). Structural, free scan, ZERO LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import label, plain, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec


def _quote(text: str, limit: int = 160) -> str:
    text = " ".join(text.split()).replace("|", "/")
    return f'"{text[:limit]}..."' if len(text) > limit else f'"{text}"'


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str:
    facts = await get_facts(ctx, session)
    trace_facts = [f for f in facts if f.predicate in ("derived_from", "traces_to")]
    req_labels = {key: name for key, name, _ in requirements(facts)}
    span_text = {
        f.subject_key: plain(f.value.get("text"))
        for f in facts
        if f.subject_type == "SourceSpan" and f.predicate == "evidence" and isinstance(f.value, dict)
    }

    md = "# Requirements Traceability Matrix (RTM)\n\n"
    if not trace_facts:
        md += "No requirement has been linked to its source evidence yet."
        return md

    md += "| Item | Relationship | Traces to |\n"
    md += "| --- | --- | --- |\n"
    for t in trace_facts:
        item = req_labels.get(t.subject_key) or t.subject_key
        target = t.object_key or plain(t.value)
        if span_text.get(target):
            target = _quote(span_text[target])
        else:
            target = req_labels.get(target) or target or "Not yet specified"
        md += f"| {item} | {label(t.predicate)} | {target} |\n"

    return md
