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

    md = f"# {TITLE}\n\n"
    md += "This draft reports only RACI assignments present in the project record. Missing roles are left blank for owner review.\n\n"
    md += "| Activity / Requirement | Responsible | Accountable | Consulted | Informed | Missing Assignments |\n| --- | --- | --- | --- | --- | --- |\n"
    missing_rows = []
    for r in racis:
        val = r.value if isinstance(r.value, dict) else {}
        roles = ("responsible", "accountable", "consulted", "informed")
        cells = [plain(val.get(role)).replace("|", "/") for role in roles]
        missing = [role.title() for role, cell in zip(roles, cells) if not cell]
        activity = plain(val.get("activity") or val.get("requirement")).replace("|", "/")
        if not activity:
            missing.insert(0, "Activity")
        md += "| " + " | ".join([activity, *cells, ", ".join(missing)]) + " |\n"
        if missing:
            missing_rows.append(plain(val.get("activity") or val.get("requirement")) or "RACI entry")
    if missing_rows:
        md += f"\n{len(missing_rows)} entries need one or more role assignments before approval.\n"

    return md
