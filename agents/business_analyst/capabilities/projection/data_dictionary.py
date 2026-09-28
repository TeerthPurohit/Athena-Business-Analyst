"""Data Dictionary Renderer. Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import filled
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Data Dictionary"
NEEDS = "business entities identified in its sources"


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    # Extraction writes subject_type="entity", subject_key=<entity name>; latest fact per entity wins.
    entities = {
        f.subject_key: f.value if isinstance(f.value, dict) else {}
        for f in facts
        if f.subject_type == "entity" and f.predicate == "described_as"
    }

    if not entities:
        return None

    md = f"# {TITLE} Specification\n\n"
    for name, val in entities.items():
        md += f"### Entity: {name}\n- **Type**: {filled(val, 'type')}\n- **Attributes**: {filled(val, 'attributes')}\n\n"

    return md
