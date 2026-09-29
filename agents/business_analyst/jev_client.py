"""OpenRouter Decisions API client for BA clarification and finding validation."""

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv

from agents.business_analyst.observability import traced


OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
# Evidence sent to the finding judge is capped here; source chunks must not exceed it, or findings
# from the tail of a chunk are judged without their evidence and silently rejected.
JEV_EVIDENCE_CHARS = 12_000


@traced("choose-inspection-tool", as_type="generation", model="typesafe/jev-1.13", input=lambda args: args["question"])
async def choose_project_inspection_tool(question: str) -> str | None:
    """Let Jev route the first read-only inspection step when confident."""
    load_dotenv(override=False)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None
    candidates = {
        "search_project_evidence": "Find relevant facts and source excerpts for a specific question.",
        "get_project_overview": "Read the overall project summary and evidence coverage.",
    }
    payload = {
        "model": JEV_MODEL,
        "state": {"user_question": question[:1000]},
        "questions": {"inspection_tool": {
            "type": "choice",
            "instructions": "Choose the best first read-only tool for a business analyst answering this project question.",
            "criteria": candidates,
        }},
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(OPENROUTER_DECISIONS_URL, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            response.raise_for_status()
            answer = response.json()["answers"]["inspection_tool"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None
    choice = answer.get("choice")
    probabilities = answer.get("probabilities") or {}
    confidence = answer.get("confidence")
    probability = probabilities.get(choice)
    if choice in candidates and isinstance(confidence, (int, float)) and confidence >= 0.75 and isinstance(probability, (int, float)) and probability >= 0.65:
        return choice
    return None


@dataclass(frozen=True)
class ChatTurnDecision:
    action: str | None = None
    tone: str = "neutral"
    topic: str = "same"
    next_step: str = "probe"


def _confident_choice(answer: Any, candidates: dict[str, str]) -> str | None:
    if not isinstance(answer, dict):
        return None
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in candidates:
        return None
    confidence = answer.get("confidence")
    probabilities = answer.get("probabilities")
    probability = probabilities.get(choice) if isinstance(probabilities, dict) else None
    if (
        isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
        and confidence >= 0.75
        and isinstance(probability, (int, float)) and not isinstance(probability, bool)
        and probability >= 0.65
    ):
        return choice
    return None


@traced("classify-chat-turn", as_type="generation", model="typesafe/jev-1.13", input=lambda args: {"message": args["message"], "pending_question": args.get("pending_question"), "options": list(args["deliverables"])}, output=lambda result: result.__dict__)
async def decide_chat_turn(
    message: str,
    deliverables: dict[str, str],
    pending_question: str | None = None,
    recent_turns: list[dict[str, str]] | None = None,
) -> ChatTurnDecision:
    """Classify one turn and its conversational context in a single Jev request."""
    load_dotenv(override=False)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return ChatTurnDecision()
    candidates = {
        "greeting": "A greeting or brief social opening with no project request or new project information.",
        "record": "The user is providing or revising project facts, scope, decisions, or answers.",
        "investigate": "The user asks Athena to inspect current project evidence and answer a question.",
        **({"answer": "The user is answering Athena's pending clarification question."} if pending_question else {}),
        **{f"deliverable_{key}": f"The user asks to create a {name} draft." for key, name in deliverables.items()},
    }
    tone_candidates = {
        "neutral": "The user is neutral or positive.",
        "frustrated": "The user expresses frustration, impatience, or dissatisfaction with Athena or the project process.",
    }
    topic_candidates = {
        "same": "The message continues the current project topic or answers the pending question.",
        "changed": "The user clearly starts a different topic or redirects the conversation.",
    }
    step_candidates = {
        "probe": "Ask or continue a useful clarification after recording project information.",
        "move_on": "Let the user lead; do not immediately ask another clarification.",
        "escalate": "The user requests deeper investigation of a project problem or an unresolved concern.",
    }
    payload = {
        "model": JEV_MODEL,
        "state": {
            "user_message": message[:1500],
            "pending_question": pending_question[:500] if pending_question else None,
            "recent_turns": [
                {"user": str(turn.get("user", ""))[:500], "assistant": str(turn.get("assistant", ""))[:500]}
                for turn in (recent_turns or [])[-4:]
                if isinstance(turn, dict)
            ],
        },
        "questions": {
            "chat_action": {
                "type": "choice",
                "instructions": "Choose the action the user explicitly requests. Treat scope changes and new information as record. A greeting must contain no substantive project information. Do not create a deliverable unless requested.",
                "criteria": candidates,
            },
            "user_tone": {"type": "choice", "instructions": "Judge the user's current tone from the conversation, without guessing hidden emotions.", "criteria": tone_candidates},
            "topic_shift": {"type": "choice", "instructions": "Compare the current message with recent turns and the pending question. Choose changed only for a clear redirect.", "criteria": topic_candidates},
            "next_step": {"type": "choice", "instructions": "Choose how Athena should proceed after handling the explicit action. Escalate means deeper project investigation, not a human handoff. Do not probe after a greeting, a topic change, or clear frustration.", "criteria": step_candidates},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(OPENROUTER_DECISIONS_URL, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            response.raise_for_status()
            answers = response.json()["answers"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return ChatTurnDecision()
    if not isinstance(answers, dict):
        return ChatTurnDecision()
    action = _confident_choice(answers.get("chat_action"), candidates)
    tone = _confident_choice(answers.get("user_tone"), tone_candidates) or "neutral"
    topic = _confident_choice(answers.get("topic_shift"), topic_candidates) or "same"
    next_step = _confident_choice(answers.get("next_step"), step_candidates) or "probe"
    if next_step == "escalate" and action != "investigate":
        next_step = "move_on"
    if action == "greeting" or tone == "frustrated" or topic == "changed":
        next_step = "move_on" if next_step == "probe" else next_step
    return ChatTurnDecision(action=action, tone=tone, topic=topic, next_step=next_step)


async def choose_chat_action(message: str, deliverables: dict[str, str], pending_question: str | None = None) -> str | None:
    """Compatibility wrapper for callers that only need the action."""
    return (await decide_chat_turn(message, deliverables, pending_question)).action

class JevDecisionError(RuntimeError):
    """Base exception for Jev Decisions API errors."""
    pass


class JevUnavailableError(JevDecisionError):
    """Raised when Jev Decisions API is unconfigured, unreachable, or returns a service error."""
    pass


class JevUncertainError(JevDecisionError):
    """Raised when Jev decision is uncertain or below confidence/probability thresholds."""
    pass



@traced("choose-clarification-gap", as_type="generation", model="typesafe/jev-1.13", input=lambda args: [{k: gap.get(k) for k in ("node_type", "field", "kind", "_score")} for gap in args["gaps"]])
async def choose_clarification_gap(
    gaps: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    instructions: str,
    conversation_context: dict[str, Any] | None = None,
) -> int | None:
    """Return the context-aware candidate index, or None to retain ranked order."""
    if len(gaps) < 2:
        return None

    load_dotenv(override=False)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    node_by_id = {str(node.get("id")): node for node in nodes}
    candidates: dict[str, str] = {}
    state: list[dict[str, Any]] = []
    for index, gap in enumerate(gaps):
        option = f"gap_{index}"
        node = node_by_id.get(str(gap.get("node_id")), {})
        attributes = node.get("attrs") or {}
        if not isinstance(attributes, dict):
            attributes = {}
        details = {
            "option": option,
            "node_type": gap.get("node_type"),
            "field": gap.get("field"),
            "placeholder": gap.get("placeholder"),
            "kind": gap.get("kind"),
            "node_attributes": {
                str(key): str(value)[:300]
                for key, value in list(attributes.items())[:8]
            },
            "deterministic_score": gap.get("_score", 0.0),
        }
        state.append(details)
        candidates[option] = (
            f"Clarify {gap.get('field')} on the {gap.get('node_type')} "
            f"(ranked score {gap.get('_score', 0.0)})."
        )

    payload = {
        "model": JEV_MODEL,
        "state": {
            "candidate_gaps": state,
            "conversation_context": conversation_context or {},
        },
        "questions": {
            "next_gap": {
                "type": "choice",
                "instructions": (
                    f"{instructions}\nUse the recent conversation and answers to avoid repeating or "
                    "contradicting what the user has said. Check recent_questions too; do not choose "
                    "a gap that reopens a decision already covered. Prefer the gap that unlocks the "
                    "feature's core behavior; ignore administrative details that do not fit this project."
                ),
                "criteria": candidates,
            }
        },
    }
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(
            OPENROUTER_DECISIONS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        response.raise_for_status()
        answer = response.json()["answers"]["next_gap"]

    choice = answer.get("choice")
    confidence = answer.get("confidence")
    probabilities = answer.get("probabilities")
    if (
        choice not in candidates
        or not isinstance(confidence, (int, float))
        or confidence < 0.75
        or not isinstance(probabilities, dict)
        or not isinstance(probabilities.get(choice), (int, float))
        or probabilities[choice] < 0.65
    ):
        return None
    return int(choice.removeprefix("gap_"))


# The Decisions API queues concurrent requests: measured 2026-09-25, 16 at once took up to 13s and
# 48 up to 44s, while a 10s timeout failed every judgment slower than that (and with it the whole
# document part). So: cap concurrent judgments, allow queueing time, and retry transient failures.
JUDGE_CONCURRENCY = int(os.getenv("BA_JEV_CONCURRENCY", "8"))
JUDGE_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
JUDGE_ATTEMPTS = 3
_judge_slots = asyncio.Semaphore(JUDGE_CONCURRENCY)


def _transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)  # timeouts, connection resets


async def _post_judgment(api_key: str, payload: dict[str, Any]) -> Any:
    last: Exception | None = None
    for attempt in range(JUDGE_ATTEMPTS):
        try:
            async with _judge_slots:
                async with httpx.AsyncClient(timeout=JUDGE_TIMEOUT) as client:
                    response = await client.post(
                        OPENROUTER_DECISIONS_URL,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                    response.raise_for_status()
                    return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            if not _transient(exc) or attempt == JUDGE_ATTEMPTS - 1:
                break
            await asyncio.sleep(2 ** attempt)
    detail = f"HTTP {last.response.status_code}" if isinstance(last, httpx.HTTPStatusError) else type(last).__name__
    raise JevUnavailableError(f"Finding validation service is unavailable ({detail}).") from last


@traced("judge-findings", as_type="evaluator", cost_model=JEV_MODEL, input=lambda args: {"findings": [{"id": f.get("id"), "claim": f.get("claim")} for f in args["findings"]], "evidence_chars": len(str(args["evidence"]))})
async def judge_findings_validity(
    findings: list[dict[str, Any]],
    evidence: str | dict[str, Any],
    *,
    instructions: str | None = None,
) -> dict[str, bool]:
    """Judge validity of findings against source evidence using OpenRouter Decisions API (Jev).

    Each finding dict in `findings` should contain:
      - 'id': identifier for mapping results back (e.g. requirement key, objective key)
      - 'claim': text representation of the finding's claim
      - optional 'details': dict with additional structured attributes
      - optional 'cited_evidence': string or list of strings specifically cited by this finding

    Returns:
      dict mapping finding 'id' -> bool (True = valid/accepted, False = invalid/rejected)
    """
    if not findings:
        return {}

    load_dotenv(override=False)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise JevUnavailableError("Finding validation is unavailable: OPENROUTER_API_KEY is not configured.")

    question_key_to_id: dict[str, str] = {}
    finding_state: list[dict[str, Any]] = []
    questions: dict[str, Any] = {}

    base_instructions = instructions or (
        "Judge whether this extracted finding is directly supported by and consistent with the "
        "source evidence. Reject hallucinations, speculation, or unsupported details."
    )

    for idx, f in enumerate(findings):
        q_key = f"finding_{idx}"
        fid = str(f["id"])
        question_key_to_id[q_key] = fid

        claim = str(f.get("claim", ""))[:1000]
        details = f.get("details")
        sanitized_details = (
            {str(k): str(v)[:300] for k, v in list(details.items())[:10]}
            if isinstance(details, dict)
            else None
        )

        item_state: dict[str, Any] = {
            "finding_id": fid,
            "claim": claim,
        }
        if sanitized_details:
            item_state["details"] = sanitized_details
        if f.get("cited_evidence") is not None:
            cited = f["cited_evidence"]
            if isinstance(cited, list):
                item_state["cited_evidence"] = [str(c)[:500] for c in cited[:10]]
            else:
                item_state["cited_evidence"] = str(cited)[:1000]
        finding_state.append(item_state)

        questions[q_key] = {
            "type": "choice",
            "instructions": f"{base_instructions}\nClaim: {claim[:200]}",
            "criteria": {
                "valid": "The finding is directly supported by and consistent with the source evidence.",
                "invalid": "The finding is unsupported, speculative, or contradicts the source evidence.",
            },
        }

    evidence_state: Any
    if isinstance(evidence, str):
        evidence_state = evidence[:JEV_EVIDENCE_CHARS]
    elif isinstance(evidence, dict):
        evidence_state = {str(k): str(v)[:500] for k, v in list(evidence.items())[:20]}
    else:
        evidence_state = str(evidence)[:JEV_EVIDENCE_CHARS]

    payload = {
        "model": JEV_MODEL,
        "state": {
            "evidence": evidence_state,
            "findings": finding_state,
        },
        "questions": questions,
    }

    data = await _post_judgment(api_key, payload)

    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        # An unusable judgment must not abort analysis of the full source part.
        # Treat every candidate as unapproved and preserve the remaining pipeline.
        return {fid: False for fid in question_key_to_id.values()}
    results: dict[str, bool] = {}
    for q_key, fid in question_key_to_id.items():
        answer = answers.get(q_key)
        if not isinstance(answer, dict):
            results[fid] = False
            continue

        choice = answer.get("choice")
        if choice is None and answer.get("bool") is not None:
            choice = "valid" if answer.get("bool") is True else "invalid"

        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities")

        if (
            choice not in ("valid", "invalid")
            or not isinstance(confidence, (int, float))
            or confidence < 0.75
            or not isinstance(probabilities, dict)
            or not isinstance(probabilities.get(choice), (int, float))
            or probabilities[choice] < 0.65
        ):
            results[fid] = False
            continue

        results[fid] = (choice == "valid")

    return results
