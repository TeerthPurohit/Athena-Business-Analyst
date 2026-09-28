"""User Story Renderer. Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import REQUIREMENTS_NEEDED, requirements, user_story
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Agile User Stories"
NEEDS = REQUIREMENTS_NEEDED


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    reqs = requirements(await get_facts(ctx, session))

    if not reqs:
        return None

    md = f"# {TITLE}\n\n"
    for _, name, val in reqs:
        md += f"### Story {name}\n{user_story(val)}\n\n"

    return md
