"""Evidence-backed Non-Functional Requirements Specification."""

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import constraint_text, plain, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Non-Functional Requirements Specification (NFR)"
NEEDS = "non-functional requirements (such as performance, security or availability expectations)"


def _text(value):
    if isinstance(value, (list, tuple)):
        return "; ".join(text for item in value if (text := plain(item)))
    return plain(value)


def _type(value):
    return str(value.get("requirement_type") or value.get("category") or "").lower().replace("-", "_")


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    nfrs = [
        (name, value) for _, name, value in requirements(await get_facts(ctx, session))
        if _type(value) == "nonfunctional"
    ]
    if not nfrs:
        return None

    md = f"# {TITLE}\n\n"
    needs_target = []
    md += "This draft includes only recorded quality requirements and targets. Unspecified thresholds remain open for stakeholder agreement.\n\n"
    for requirement_id, value in nfrs:
        task = _text(value.get("task"))
        obj = _text(value.get("object"))
        statement = task or obj or _text(value.get("outcomes"))
        if task and obj and obj.casefold() not in task.casefold():
            statement += f" — {obj}"
        if statement and not statement.lower().startswith(("the system ", "the application ", "the service ")):
            statement = "The system shall " + statement[:1].lower() + statement[1:]
        constraints = [
            text for item in (value.get("constraints") or [])
            if (text := constraint_text(item))
        ]
        dimension = plain(value.get("functional_area"))
        md += f"### {requirement_id}\n\n"
        if dimension:
            md += f"**Quality area:** {dimension}\n\n"
        if statement:
            md += f"**Requirement:** {statement}\n\n"
        if constraints:
            md += "**Recorded target or constraint:** " + "; ".join(constraints) + "\n\n"
        else:
            needs_target.append(requirement_id)
        if value.get("trigger"):
            md += f"**Trigger:** {plain(value.get('trigger'))}\n\n"
        if value.get("outcomes"):
            md += "**Expected behavior:** " + _text(value.get("outcomes")) + "\n\n"

    if needs_target:
        md += f"## Requirements needing measurable targets ({len(needs_target)})\n\n"
        md += "Agree an applicable threshold or confirm that the requirement has no measurable target:\n\n"
        md += "".join(f"- {requirement_id}\n" for requirement_id in needs_target)
    return md
