"""Options Analysis Renderer. Narrated trade-off narrative via llm_client.get_structured_output
with the DB prompt ba_options_narrative_v2."""
import hashlib
import json
import logging

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import plain
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec, BaProject
from agents.business_analyst.llm_client import get_structured_output
from models.agent_prompt import fetch_prompt

logger = logging.getLogger(__name__)

TITLE = "Options & Trade-Off Analysis"
NEEDS = "solution options to compare"


class TradeoffNarrative(BaseModel):
    recommendation: str
    tradeoff_summary: str


def _option_line(value) -> str:
    fields = dict(value) if isinstance(value, dict) else {"title": value}
    title = plain(fields.pop("title", None))
    details = plain(fields)
    return f"- **{title}**: {details}" if title and details else f"- {title or details}"


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    options = [f for f in facts if f.subject_type in ("Option", "SolutionOption")]

    if not options:
        return None

    opt_lines = [_option_line(o.value) if plain(o.value) else f"- {o.subject_key}" for o in options]
    input_json = json.dumps(
        {"options": [{"id": str(option.subject_key), "value": option.value} for option in options]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )

    try:
        system_prompt = await fetch_prompt("9", "ba_options_narrative_v2")
        fingerprint = hashlib.sha256((system_prompt + "\n" + input_json).encode("utf-8")).hexdigest()
        project = await session.get(BaProject, ctx.project_id)
        cached = (project.settings or {}).get("options_document_analysis") if project and project.org_id == ctx.org_id else None
        narrative = None
        if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
            try:
                narrative = TradeoffNarrative.model_validate(cached.get("analysis"))
            except Exception:
                logger.info("BA_OPTIONS_ANALYSIS_CACHE_INVALID project_id=%s", ctx.project_id)
        if narrative is None:
            narrative = await get_structured_output(
                system_prompt=system_prompt,
                user_prompt=input_json,
                response_model=TradeoffNarrative,
                agent_id="9",
                name="write-options-analysis",
                session_id=ctx.project_id,
            )
            if project and project.org_id == ctx.org_id:
                await session.refresh(project, attribute_names=["settings"])
                updated_settings = dict(project.settings or {})
                updated_settings["options_document_analysis"] = {
                    "fingerprint": fingerprint,
                    "analysis": narrative.model_dump(mode="json"),
                }
                project.settings = updated_settings
                await session.flush()
        rec = narrative.recommendation
        summary = narrative.tradeoff_summary
    except Exception as exc:
        logger.warning("BA_OPTIONS_NARRATIVE_DEGRADED project_id=%s reason=%s", ctx.project_id, exc)
        rec = "The recommendation could not be written just now. Regenerate this draft to try again."
        summary = "The evaluation summary could not be written just now. Regenerate this draft to try again."

    md = f"# {TITLE}\n\n"
    md += f"## Recommendation\n{rec}\n\n"
    md += f"## Evaluation Summary\n{summary}\n\n"
    md += "## Analyzed Options\n" + "\n".join(opt_lines)
    return md
