"""Acceptance Criteria Renderer. Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import (
    REQUIREMENTS_NEEDED,
    filled,
    plain,
    requirements,
    unspecified,
)
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Acceptance Criteria Specification"
NEEDS = REQUIREMENTS_NEEDED


def _clauses(keyword: str, items: list[str], blank: str) -> str:
    items = items or [unspecified(blank)]
    return "".join(f"- **{keyword if i == 0 else 'And'}** {item}\n" for i, item in enumerate(items))


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    reqs = requirements(await get_facts(ctx, session))

    if not reqs:
        return None

    md = f"# {TITLE}\n\n"
    for _, name, val in reqs:
        preconditions = [text for text in map(plain, val.get("preconditions") or []) if text]
        outcomes = [text for text in map(plain, val.get("outcomes") or []) if text]
        md += f"### {name}\n"
        md += _clauses("Given", preconditions, "precondition")
        md += f"- **When** {filled(val, 'trigger')}\n"
        md += _clauses("Then", outcomes, "outcome") + "\n"

    return md
