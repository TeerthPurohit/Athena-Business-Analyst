"""Risk Log Renderer. Structural, zero LLM calls. Reads business_context["risks"] records."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import business_context, records_markdown
from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Project Risk Log"
NEEDS = "risks in its business context"


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    body = records_markdown(await business_context(ctx, session), "risks", "Risk")
    return f"# {TITLE}\n\n{body}" if body else None
