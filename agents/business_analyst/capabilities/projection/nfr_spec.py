"""NFR Spec Renderer (Non-Functional Requirements Specification). Structural, zero LLM calls."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import (
    constraint_text,
    requirements,
    unspecified,
    user_story,
)
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Non-Functional Requirements Specification (NFR)"
NEEDS = "non-functional requirements (such as performance, security or availability expectations)"


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    nfrs = [
        (name, val) for _, name, val in requirements(await get_facts(ctx, session))
        if val.get("category") == "nonfunctional"
    ]

    if not nfrs:
        return None

    md = f"# {TITLE}\n\n"
    for name, val in nfrs:
        targets = [text for text in map(constraint_text, val.get("constraints") or []) if text]
        md += (
            f"### {name}\n"
            f"- **Requirement**: {user_story(val)}\n"
            f"- **Targets**: {'; '.join(targets) or unspecified('measurable target')}\n\n"
        )

    return md
