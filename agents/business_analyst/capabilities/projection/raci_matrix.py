"""RACI Matrix Renderer. Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import plain
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "RACI Matrix"
NEEDS = "responsibility assignments (who is responsible, accountable, consulted and informed)"


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    racis = [f for f in facts if f.predicate == "raci" or f.subject_type == "RACI"]

    if not racis:
        return None

    md = f"# {TITLE}\n\n| Activity / Requirement | Responsible | Accountable | Consulted | Informed |\n| --- | --- | --- | --- | --- |\n"
    for r in racis:
        val = r.value if isinstance(r.value, dict) else {}
        cells = [plain(val.get(role)) or "-" for role in ("responsible", "accountable", "consulted", "informed")]
        md += f"| {plain(val.get('activity')) or r.subject_key} | " + " | ".join(cells) + " |\n"

    return md
