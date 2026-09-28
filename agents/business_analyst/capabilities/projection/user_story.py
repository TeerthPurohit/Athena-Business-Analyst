"""User Story Renderer. Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import REQUIREMENTS_NEEDED, plain, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Agile User Stories"
NEEDS = REQUIREMENTS_NEEDED


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    reqs = requirements(await get_facts(ctx, session))

    if not reqs:
        return None

    md = f"# {TITLE}\n\n"
    stories = []
    clarification = []
    for _, name, val in reqs:
        stakeholder = plain(val.get("stakeholder"))
        task = plain(val.get("task"))
        obj = plain(val.get("object"))
        benefit = plain(val.get("benefit"))
        missing = [
            field for field, value in (
                ("stakeholder", stakeholder), ("task", task or obj), ("business benefit", benefit),
            ) if not value
        ]
        if missing:
            clarification.append((name, missing))
            continue

        role = stakeholder.strip()
        if not role.lower().startswith(("a ", "an ", "the ")):
            role = f"{'an' if role[:1].lower() in 'aeiou' else 'a'} {role}"
        want = task.strip()
        if obj and obj.casefold() not in want.casefold():
            want += f" ({obj})"
        stories.append((name, f"As {role}, I want to {want}, so that {benefit}."))

    md += "Stories are included only when the project record identifies a stakeholder, task, and business benefit. All records remain drafts for stakeholder review.\n\n"
    if stories:
        for name, story in stories:
            md += f"### {name}\n{story}\n\n"
    else:
        md += "No complete user stories can be written from the current evidence. Review the Requirements Register for the detailed source records.\n\n"
    if clarification:
        md += f"## Stories needing clarification ({len(clarification)})\n\n"
        md += "".join(f"- {name}: confirm {', '.join(missing)}.\n" for name, missing in clarification)

    return md
