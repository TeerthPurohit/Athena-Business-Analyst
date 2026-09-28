"""Glossary Renderer. Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import plain
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
    defined = []
    needs_definition = []
    for t in terms:
        val = t.value if isinstance(t.value, dict) else {"definition": t.value}
        term = plain(val.get("term")) or plain(t.subject_key)
        definition = plain(val.get("definition"))
        if term and definition:
            defined.append((term, definition))
        elif term:
            needs_definition.append(term)
    for term, definition in defined:
        md += f"### {term}\n{definition}\n\n"
    if needs_definition:
        md += f"## Terms needing a definition ({len(needs_definition)})\n\n"
        md += "".join(f"- {term}\n" for term in needs_definition)
    if not defined and not needs_definition:
        md += "No glossary terms with a recorded name or definition are available yet.\n"

    return md
