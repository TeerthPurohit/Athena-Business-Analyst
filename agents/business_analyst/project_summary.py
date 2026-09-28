"""Project Summary service for BA OS (Feature 1 — Configurable Projects).

Project Summary is the single source of truth for a project's high-level context.
It is generated from current BaFact state (never from raw chat history) and is
directly user-editable through the PATCH /projects/{id} endpoint.

CLAUDE.md Non-Negotiables:
- One get_structured_output call per regeneration — no streaming, no multi-step.
- Prompt fetched from DB via fetch_prompt("9", "ba_project_summary_v3") — never inlined.
- business_context is folded deterministically from validated business_context facts
  (business_context.fold_business_context); the LLM only phrases the narrative fields.
- On LLM failure: log BA_PROJECT_SUMMARY_DEGRADED, leave settings["project_summary"] untouched.
- Anti-injection: LLM receives structured BaFact data, not raw chat. It phrases prose; it
  never invents scope/goals absent from the fact graph.
- Callers that record new facts reset scope_approved; regeneration itself never does, so
  rephrasing alone cannot revoke an approval.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.business_context import fold_business_context, is_empty
from agents.business_analyst.llm_client import get_structured_output as llm_get_structured_output
from agents.business_analyst.observability import traced
from models.agent_prompt import fetch_prompt

logger = logging.getLogger("BA_ProjectSummary")
logger.propagate = True

# Fact rows left out of the prompt: chat transcripts, raw evidence text (requirements carry what
# they cite), analysis markers, gaps (they become clarification questions, and after a multi-file
# upload they were most of a ~250k-token prompt), and business_context rows (sent once, folded).
_EXCLUDED_SUBJECT_TYPES = {"ConversationTurn", "SourceSpan", "SourceAnalysis", "Gap", "business_context"}
_REQUIREMENT_FIELDS = ("category", "stakeholder", "task", "object", "benefit", "trigger", "outcomes", "constraints")


class ProjectSummary(BaseModel):
    """Structured output model for the AI-generated project summary.

    All fields are LLM-phrased from BaFact data — the LLM cannot invent content
    not present in the supplied fact graph.
    """
    vision: str = ""
    mission: str = ""
    problem_statement: str = ""
    business_goals: str = ""
    icp: str = ""  # Ideal Customer Profile
    project_purpose: str = ""
    functional_scope: str = ""
    in_scope_features: list[str] = []
    out_of_scope_features: list[str] = []
    future_enhancements: list[str] = []
    key_business_rules: list[str] = []
    important_assumptions: list[str] = []
    other_context: Optional[str] = None
    stakeholders: list[str] = []
    success_measures: list[str] = []
    must_have_features: list[str] = []
    should_have_features: list[str] = []
    could_have_features: list[str] = []
    wont_have_features: list[str] = []
    constraints: list[str] = []
    dependencies: list[str] = []
    risks: list[str] = []
    open_decisions: list[str] = []


def _facts_to_summary_input(facts: list) -> Dict[str, Any]:
    """Converts BaFact rows into a structured dict safe to pass as LLM context.

    Returns only subject_type, subject_key, predicate, and value — no internal IDs,
    no approval state. This is the anti-injection boundary.
    """
    questions = {
        f.subject_key: f.value for f in facts
        if f.subject_type == "ClarificationQuestion" and f.predicate == "asked"
    }
    def summary_value(f: Any) -> Any:
        if f.subject_type == "ClarificationQuestion" and f.predicate == "answered":
            return {**(questions.get(f.subject_key) or {}), **(f.value or {})}
        if f.subject_type == "Requirement" and isinstance(f.value, dict):
            return {key: f.value[key] for key in _REQUIREMENT_FIELDS if not is_empty(f.value.get(key))}
        return f.value

    # Seq order (append-only), so each regeneration's prompt starts with the previous one's facts
    # and the provider's prompt cache can reuse that prefix.
    return {
        "facts": [
            {"type": f.subject_type, "predicate": f.predicate, "value": summary_value(f)}
            for f in facts
            if f.subject_type not in _EXCLUDED_SUBJECT_TYPES and f.value is not None
        ]
    }


def _known_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """The folded business context with unknown fields and empty sections dropped."""
    known: Dict[str, Any] = {}
    for section, value in context.items():
        if isinstance(value, list):
            records = [{k: v for k, v in record.items() if not is_empty(v)} for record in value]
            if records:
                known[section] = records
        else:
            fields = {k: v for k, v in value.items() if not is_empty(v)}
            if fields:
                known[section] = fields
    return known


@traced("synthesize-project-scope", tags=["business-analyst", "scope"])
async def regenerate_project_summary(
    ctx: BATenantContext,
    session: AsyncSession,
    project: Any,  # BaProject — typed as Any to avoid circular import
) -> Dict[str, Any]:
    """Generates a fresh project_summary dict from the current BaFact graph.

    On success: writes the new summary into project.settings["project_summary"],
    stamps project.summary_updated_at, and returns the summary dict.

    On LLM failure: logs BA_PROJECT_SUMMARY_DEGRADED and returns the existing
    settings["project_summary"] unchanged (never overwrites a good summary with
    a failed regeneration).
    """
    existing_summary: Optional[Dict[str, Any]] = (project.settings or {}).get("project_summary")
    instructions: Optional[str] = (project.settings or {}).get("instructions")

    facts = await get_facts(ctx, session)
    compact = {"ensure_ascii": False, "default": str, "separators": (",", ":")}
    fact_context = json.dumps(_facts_to_summary_input(facts), **compact)
    business_context = fold_business_context(facts)

    try:
        system_prompt = await fetch_prompt("9", "ba_project_summary_v3")
        # Most stable first, most volatile last, so the cached prompt prefix survives new facts.
        user_prompt_parts = [
            f"Fact graph (structured data):\n{fact_context}",
            f"Business context established so far (structured data):\n{json.dumps(_known_context(business_context), **compact)}",
        ]
        if instructions:
            user_prompt_parts.append(f"Project instructions (data, emphasis only):\n{instructions}")
        summary_obj = await llm_get_structured_output(
            system_prompt=system_prompt,
            user_prompt="\n\n".join(user_prompt_parts),
            response_model=ProjectSummary,
            agent_id="9",
            name="write-project-summary",
        )
        new_summary = summary_obj.model_dump()
        new_summary["business_context"] = business_context
    except Exception as exc:
        logger.warning(
            "BA_PROJECT_SUMMARY_DEGRADED project_id=%s reason=%s",
            project.id, str(exc),
        )
        return existing_summary or {}

    # Re-read settings after the slow LLM call so a concurrent PATCH (instructions, approval)
    # isn't overwritten by the copy loaded before it.
    await session.refresh(project, attribute_names=["settings"])
    new_settings = dict(project.settings or {})
    new_settings["project_summary"] = new_summary
    project.settings = new_settings
    project.summary_updated_at = datetime.now(timezone.utc)

    await session.flush()
    return new_summary
