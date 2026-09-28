"""Glossary Renderer. Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import filled, plain
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Business Glossary"
NEEDS = "business terms with definitions"


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    terms = [f for f in facts if f.subject_type in ("Term", "GlossaryTerm")]

    if not terms:
        return None

    md = f"# {TITLE}\n\n"
    for t in terms:
        val = t.value if isinstance(t.value, dict) else {"definition": t.value}
        md += f"### {plain(val.get('term')) or t.subject_key}\n{filled(val, 'definition')}\n\n"

    return md
