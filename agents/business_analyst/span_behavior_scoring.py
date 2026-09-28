"""Behavior scoring for final analyst answers using Span-01 through OpenRouter."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv

from agents.business_analyst.cost_log import record_call
from agents.business_analyst.observability import _mask, observation, record_error


OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_SPAN_MODEL = "respan/span-01"
MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_CHARS = 1_500
MAX_ANSWER_CHARS = 12_000
_TOKEN_PATTERN = re.compile(r"\b(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{15,}|AIza[0-9A-Za-z_-]{30,})\b")
_SECRET_FIELD_PATTERN = re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"]?)([^\s'\",}]+)")
_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/-]+=*)")
_PRIVATE_KEY_PATTERN = re.compile(r"(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----")

BEHAVIORS: tuple[dict[str, str], ...] = (
    {
        "id": "grounded_in_project_evidence",
        "label": "Grounded in project evidence",
        "instructions": "Does the assistant's answer make project-specific factual claims supported by the supplied project evidence?",
        "true": "Project-specific factual claims are supported by the supplied evidence; uncertainty and recommendations are clearly distinguished.",
        "false": "The answer contains unsupported project-specific claims or presents speculation as established fact.",
    },
    {
        "id": "unsupported_claims",
        "label": "Unsupported project claims",
        "instructions": "Does the assistant state a project-specific fact, source, decision, or status that is not supported by the supplied project evidence?",
        "true": "At least one project-specific claim is unsupported, contradicted, or presented with more certainty than the evidence allows.",
        "false": "The answer stays within the supplied evidence or clearly labels uncertainty and recommendations.",
    },
    {
        "id": "respected_user_scope",
        "label": "Respected the user's scope",
        "instructions": "Does the assistant stay within the action or answer the user requested?",
        "true": "The assistant addresses the request without claiming or making unrequested project decisions or changes.",
        "false": "The assistant expands the task, invents a decision, or claims an unrequested project change.",
    },
    {
        "id": "asked_for_needed_clarification",
        "label": "Asked when clarification was needed",
        "instructions": "When a material detail needed for a safe, accurate answer is missing, does the assistant ask a focused clarification instead of guessing?",
        "true": "The assistant asks a focused question when missing information materially affects the answer.",
        "false": "The answer guesses about a material missing detail instead of asking for clarification.",
    },
)


def _enabled() -> bool:
    return os.getenv("BA_SPAN_SCORING_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _bounded_evidence(tools_used: list[dict[str, Any]]) -> list[dict[str, str]]:
    bounded: list[dict[str, str]] = []
    for item in tools_used[:MAX_EVIDENCE_ITEMS]:
        raw = json.dumps(item.get("result"), ensure_ascii=False, default=str)
        if len(raw) > MAX_EVIDENCE_CHARS:
            raw = raw[:MAX_EVIDENCE_CHARS] + "…"
        bounded.append({"tool": str(item.get("tool", "project_tool")), "result": raw})
    return bounded


def _redact_credentials(value: Any) -> Any:
    if isinstance(value, str):
        value = _PRIVATE_KEY_PATTERN.sub("[redacted private key]", value)
        value = _SECRET_FIELD_PATTERN.sub(r"\1[redacted]", value)
        value = _BEARER_PATTERN.sub(r"\1[redacted]", value)
        return _TOKEN_PATTERN.sub("[redacted token]", value)
    if isinstance(value, dict):
        return {key: _redact_credentials(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_credentials(item) for item in value]
    return value


def _unavailable(model: str, message: str) -> dict[str, Any]:
    return {"status": "unavailable", "model": model, "message": message, "behaviors": []}


async def score_project_answer_behaviors(
    question: str,
    answer: str,
    tools_used: list[dict[str, Any]],
    *,
    project_id: str,
    org_id: str,
) -> dict[str, Any]:
    """Return Span-01 behavior probabilities for one final answer.

    The request contains only the current question, a bounded set of project-tool results,
    and the final answer. Contact details and common credential patterns are masked first.
    Scoring is fail-open: provider or response errors never suppress the analyst answer.
    """
    load_dotenv(override=False)
    model = os.getenv("BA_SPAN_MODEL", DEFAULT_SPAN_MODEL).strip() or DEFAULT_SPAN_MODEL
    if not _enabled():
        return {"status": "disabled", "model": model, "behaviors": []}

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return {"status": "disabled", "model": model, "behaviors": []}

    state = _redact_credentials(_mask(data={
        "question": question[:4_000],
        "project_evidence": _bounded_evidence(tools_used),
        "assistant_response": answer[:MAX_ANSWER_CHARS],
    }))
    questions = {
        behavior["id"]: {
            "type": "noul",
            "instructions": behavior["instructions"],
            "criteria": {"true": behavior["true"], "false": behavior["false"]},
        }
        for behavior in BEHAVIORS
    }
    payload = {"model": model, "state": state, "questions": questions}

    with observation(
        "score-answer-behaviors",
        as_type="evaluator",
        input=state,
        model=model,
        session_id=project_id,
        tags=["business-analyst", "span-01", "evaluation"],
        metadata={"org_id": org_id, "behavior_count": len(BEHAVIORS)},
    ) as obs:
        try:
            timeout = float(os.getenv("BA_SPAN_TIMEOUT_SECONDS", "8"))
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    OPENROUTER_DECISIONS_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            answers = data.get("answers") if isinstance(data, dict) else None
            if not isinstance(answers, dict):
                raise ValueError("OpenRouter returned no behavior answers.")

            scores: list[dict[str, Any]] = []
            for behavior in BEHAVIORS:
                answer_data = answers.get(behavior["id"])
                probability = answer_data.get("noul") if isinstance(answer_data, dict) else None
                if not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
                    raise ValueError(f"OpenRouter returned an invalid score for {behavior['id']}.")
                scores.append({
                    "id": behavior["id"],
                    "label": behavior["label"],
                    "p_present": round(float(probability), 4),
                })

            usage = data.get("usage") if isinstance(data, dict) else None
            if isinstance(usage, dict):
                input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
                output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
                record_call(
                    model=model,
                    input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                    output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                    cost=usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None,
                )
            else:
                record_call(model=model)

            result = {"status": "completed", "model": model, "behaviors": scores}
            obs.update(output=result)
            return result
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            record_error(obs, exc)
            return _unavailable(model, f"Span-01 could not score this answer ({type(exc).__name__}).")
