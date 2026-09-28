"""Test Case Renderer. Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import REQUIREMENTS_NEEDED, filled, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Test Case Suite"
NEEDS = REQUIREMENTS_NEEDED


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    reqs = requirements(await get_facts(ctx, session))

    if not reqs:
        return None

    md = f"# {TITLE}\n\n"
    for idx, (_, name, val) in enumerate(reqs, 1):
        md += (
            f"### TC-{idx:03d} (verifies {name})\n"
            f"- **Scenario**: {filled(val, 'stakeholder')} can {filled(val, 'task')}\n"
            f"- **Preconditions**: {filled(val, 'preconditions')}\n"
            f"- **Action**: {filled(val, 'trigger')}\n"
            f"- **Expected result**: {filled(val, 'outcomes')}\n\n"
        )

    return md
