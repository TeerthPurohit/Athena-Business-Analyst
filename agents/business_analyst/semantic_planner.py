"""Semantic Planner for BA OS (§3, §4, §8).

CLAUDE.md Non-Negotiables:
- LLM -> ProjectIR ONLY.
- Prompt fetched from agent_prompts DB table (agent_id="9", prompt_key="ba_semantic_planner_v2").
- NO capability selection, NO execution DAG ordering.
- On LLM failure: log BA_SEMANTIC_PLANNER_DEGRADED and raise. There is no heuristic fallback —
  a fabricated IR would be judged against the same text and could be persisted as facts.
"""
import logging
from typing import Any, Dict, Optional

from agents.business_analyst.ir import ProjectIR
from agents.business_analyst.llm_client import get_structured_output as llm_get_structured_output
from models.agent_prompt import fetch_prompt
from agents.business_analyst.requirements import RequirementExtraction


logger = logging.getLogger("BA_Semantic_Planner")
logger.propagate = True


def build_project_ir_from_dict(data: Dict[str, Any]) -> ProjectIR:
    """Validates and constructs a ProjectIR instance from a dictionary."""
    return ProjectIR.model_validate(data)


async def build_project_ir_llm(user_request: str, session_id: Optional[str] = None) -> ProjectIR:
    """Parses chat or document text into a ProjectIR with the ba_semantic_planner_v2 prompt."""
    if not user_request or not user_request.strip():
        return ProjectIR(project_name="")

    try:
        system_prompt = await fetch_prompt("9", "ba_semantic_planner_v2")
        return await llm_get_structured_output(
            system_prompt=system_prompt,
            user_prompt=user_request,
            response_model=ProjectIR,
            agent_id="9",
            name="parse-project-context",
            session_id=session_id,
        )
    except Exception as exc:
        logger.warning("BA_SEMANTIC_PLANNER_DEGRADED agent_id=9 session_id=%s reason=%s", session_id, str(exc))
        raise RuntimeError("Project context analysis is unavailable right now.") from exc


async def build_requirement_extraction_llm(
    source_text: str,
    session_id: Optional[str] = None,
) -> RequirementExtraction:
    """Extracts cited requirement candidates without a fabricated fallback."""
    if not source_text.strip():
        return RequirementExtraction()

    system_prompt = await fetch_prompt("9", "ba_requirement_extraction_v1")
    extraction = await llm_get_structured_output(
        system_prompt=system_prompt,
        user_prompt=source_text,
        response_model=RequirementExtraction,
        agent_id="9",
        name="draft-requirements",
        session_id=session_id,
    )
    if not isinstance(extraction, RequirementExtraction):
        raise RuntimeError("BA requirement extractor returned an invalid response type.")
    return extraction
