"""Clarification Engine for BA OS Smart Clarification (Feature 3).

Phrases clarifying questions and conflict notices via LLM (one get_structured_output
call each). All ranking and conflict detection is deterministic (gap_ranking.py).

Q&A tracking is via BaFact:
  subject_type = "ClarificationQuestion"
  subject_key  = sha256(node_id + ":" + field)[:32]
  predicate    = "asked"  | "answered"
  value        = {"question": "..."} | {"answer": "..."}

CLAUDE.md Non-Negotiables:
- Prompts fetched from DB via fetch_prompt("9", key) — never inlined.
- On LLM failure: degraded-mode returns a templated fallback string — NOT a hardcoded string
  in code; the fallback is assembled from the gap's structured fields.
- Synthetic BaSource row (kind="system") created lazily per project on first use.
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.facts import BATenantContext, assert_fact, get_facts
from agents.business_analyst.models import BaProject, BaSource
from agents.business_analyst.quality.gap_ranking import detect_value_conflict, rank_gaps
from agents.business_analyst.quality.ambiguity import detect_ambiguities
from agents.business_analyst.jev_client import choose_clarification_gap
from agents.business_analyst.llm_client import get_structured_output as llm_get_structured_output
from agents.business_analyst.observability import traced
from models.agent_prompt import fetch_prompt

logger = logging.getLogger("BA_Clarification")
logger.propagate = True

PROJECT_DISCOVERY_QUESTIONS = {
    "purpose": "What are you hoping this project will make easier or possible?",
    "problem": "What feels hardest today that you want this project to fix?",
    "stakeholders": "Who else, if anyone, will use or approve the result?",
    "success_measures": "What would make you say this is working well?",
    "must_have": "For a first version, what does it absolutely need to do?",
    "should_have": "What would be useful to add after the first version?",
    "could_have": "What would be a nice extra if time allows?",
    "wont_have": "Is there anything you already know should stay out of scope?",
    "constraints": "Is there a deadline or technical limit I should keep in mind?",
    "risks_dependencies": "Is this relying on anything that could affect how it gets built?",
}

MAX_CLARIFICATIONS_PER_THREAD = 3

CLARIFICATION_STYLE_GUIDANCE = """\
Speak like a thoughtful product partner helping the project owner shape a feature, not like a form.
Use the project and conversation context below. Name the feature naturally when it helps show you understood it.
Ask exactly one short, friendly, directly answerable question (ideally under 22 words).
Ask about a decision that will change what gets built. Prefer the feature's core behavior and user flow before generic project administration.
Do not ask for information already stated, and respect details the user has explicitly ruled out (including having no other stakeholders).
Avoid generic phrases such as “this field is unspecified” or “could you clarify the field.” Return only the question, with no acknowledgment or explanation."""


def _stakeholders_already_clear(messages: List[str], answers: List[Dict[str, str]]) -> bool:
    context = [message.lower() for message in messages]
    context.extend(
        f"{item.get('question', '')} {item.get('answer', '')}".lower()
        for item in answers
    )
    return any(phrase in text for text in context for phrase in (
        "no stakeholders", "no stakeholder", "no other stakeholders", "no other stakeholder",
        "personal project", "just me", "only me", "i am the stakeholder", "i'm the stakeholder",
        "im the stakeholder", "i am responsible for this",
    ))


def _starts_new_feature_thread(message: str) -> bool:
    text = message.lower()
    return any(phrase in text for phrase in (
        "another feature", "a new feature", "new feature", "also add", "add another",
        "also want to add", "want to add", "would like to add", "in addition,",
        "want my project to also", "want the project to also", "want this project to also",
    ))


def _fallback_question(ranked_gap: Dict[str, Any], node_context: Dict[str, Any]) -> str:
    field = str(ranked_gap.get("field") or "").lower()
    node_type = ranked_gap.get("node_type", "feature")
    attrs = node_context.get("attrs") or {}
    feature = next((
        str(attrs[key]).strip() for key in ("name", "title", "feature", "summary", "description")
        if attrs.get(key) and not str(attrs[key]).startswith("<UNSPECIFIED:")
    ), "")
    if node_type == "ProjectDiscovery":
        return PROJECT_DISCOVERY_QUESTIONS.get(field, "What should the first version help you do?")
    if feature:
        if any(token in field for token in ("trigger", "event", "condition")):
            return f"When should {feature} happen?"
        if any(token in field for token in ("actor", "user", "stakeholder", "owner")):
            return f"Who do you picture using {feature}, if anyone else?"
        if any(token in field for token in ("outcome", "result", "success", "acceptance")):
            return f"What should someone be able to do with {feature}?"
        return f"For {feature}, what should {field.replace('_', ' ')} look like in practice?"
    placeholder = str(ranked_gap.get("placeholder") or field.replace("_", " ")).strip()
    return f"What should {placeholder} look like for this feature?"


# ---------------------------------------------------------------------------
# Structured output schemas (one field each — LLM returns exactly this)
# ---------------------------------------------------------------------------

class ClarificationQuestion(BaseModel):
    question: str


class ConflictNotice(BaseModel):
    notice: str


# ---------------------------------------------------------------------------
# Subject key hashing — mirrors compute_pair_key style from similarity.py
# ---------------------------------------------------------------------------

def gap_subject_key(node_id: str, field: str) -> str:
    """Deterministic subject_key for a gap — used as the BaFact identifier."""
    raw = f"{node_id}:{field}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Synthetic system source — created lazily per project
# ---------------------------------------------------------------------------

async def _get_or_create_system_source(
    ctx: BATenantContext,
    session: AsyncSession,
) -> BaSource:
    """Returns the project's system-tier BaSource, creating it once if absent.

    kind="system" signals this is an AI-generated internal record, not user-submitted.
    """
    import uuid as _uuid

    stmt = select(BaSource).where(
        BaSource.project_id == ctx.project_id,
        BaSource.org_id == ctx.org_id,
        BaSource.kind == "system",
    ).limit(1)
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing:
        return existing

    source = BaSource(
        id=str(_uuid.uuid4()),
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        kind="system",
        tier="internal",
        ref="ba_clarification_engine",
        content_hash=hashlib.sha256(f"system:{ctx.project_id}".encode()).hexdigest(),
        captured_at=datetime.now(timezone.utc),
    )
    session.add(source)
    await session.flush()
    return source


# ---------------------------------------------------------------------------
# LLM phrasing
# ---------------------------------------------------------------------------

async def phrase_clarification_question(
    ranked_gap: Dict[str, Any],
    session_id: Optional[str] = None,
    *,
    node_context: Optional[Dict[str, Any]] = None,
    recent_messages: Optional[List[str]] = None,
    recent_questions: Optional[List[str]] = None,
    previous_answers: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Phrase one unresolved detail as a concise, context-aware question."""
    node_type = ranked_gap.get("node_type", "requirement")
    field = ranked_gap.get("field", "field")
    placeholder = ranked_gap.get("placeholder", field)
    node_context = node_context or {}
    attrs = node_context.get("attrs") or {}
    known_attrs = {
        str(key): str(value)[:500]
        for key, value in attrs.items()
        if value is not None and not str(value).startswith("<UNSPECIFIED:")
    }
    project_context = node_context.get("project_context") or {}
    recent_messages = recent_messages or []
    recent_questions = recent_questions or []
    previous_answers = previous_answers or []
    fallback = _fallback_question(ranked_gap, node_context)

    try:
        system_prompt = await fetch_prompt("9", "ba_clarification_question_v1")
        system_prompt = (
            f"{system_prompt}\n\n{CLARIFICATION_STYLE_GUIDANCE}\n"
            "Ask only for a decision that is still genuinely needed. Do not repeat, reword, "
            "or split a decision already covered by the conversation or an earlier question. "
            "Stop after at most three questions about this feature."
        )
        conversation_lines = [f"- User: {message[:800]}" for message in recent_messages[-8:]]
        question_lines = [f"- {question[:300]}" for question in recent_questions[-3:]]
        answer_lines = [
            f"- Athena asked: {item.get('question', '')[:300]} | User answered: {item.get('answer', '')[:500]}"
            for item in previous_answers[-5:]
        ]
        user_prompt = (
            f"Gap details:\n"
            f"  node_type: {node_type}\n"
            f"  field: {field}\n"
            f"  placeholder: {placeholder}\n"
            f"  kind: {ranked_gap.get('kind', 'unspecified')}\n"
            f"  feature/project details: {known_attrs or 'No structured details yet'}\n"
            f"  wider project context: {project_context or 'No additional context'}\n"
            f"Recent conversation:\n{chr(10).join(conversation_lines) or '- No user messages yet'}\n"
            f"Questions already asked about this feature:\n{chr(10).join(question_lines) or '- None yet'}\n"
            f"Details already answered:\n{chr(10).join(answer_lines) or '- None yet'}"
        )
        result = await llm_get_structured_output(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=ClarificationQuestion,
            agent_id="9",
            name="phrase-clarification-question",
            session_id=session_id,
        )
        if isinstance(result, ClarificationQuestion) and result.question:
            question = result.question.strip()
            if "?" in question:
                return question[:question.find("?") + 1].strip()
            return question.rstrip(".!") + "?"
    except Exception as exc:
        logger.warning(
            "BA_CLARIFICATION_QUESTION_DEGRADED gap=%s:%s reason=%s",
            ranked_gap.get("node_id"), field, str(exc),
        )

    return fallback


async def phrase_conflict_notice(
    conflict: Dict[str, Any],
    session_id: Optional[str] = None,
) -> str:
    """Phrases a detected conflict as a stakeholder-facing notice.

    Degraded mode: returns a templated string assembled from conflict fields.
    """
    prior = conflict.get("prior", "")
    new = conflict.get("new", "")

    fallback = (
        f"A conflict was detected: the previously recorded answer was '{prior}', "
        f"but the new answer is '{new}'. Please confirm which should stand."
    )

    try:
        system_prompt = await fetch_prompt("9", "ba_conflict_notice_v1")
        user_prompt = f"Prior answer: {prior}\nNew answer: {new}"
        result = await llm_get_structured_output(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=ConflictNotice,
            agent_id="9",
            name="phrase-conflict-notice",
            session_id=session_id,
        )
        if isinstance(result, ConflictNotice) and result.notice:
            return result.notice
    except Exception as exc:
        logger.warning("BA_CONFLICT_NOTICE_DEGRADED reason=%s", str(exc))

    return fallback


# ---------------------------------------------------------------------------
# Next question: rank → dedup → phrase → record
# ---------------------------------------------------------------------------

@traced("ask-clarification-question", tags=["business-analyst", "clarification"], output=lambda result: (result or {}).get("question"))
async def get_next_clarification(
    ctx: BATenantContext,
    session: AsyncSession,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Returns the next unresolved clarifying question, or None if all gaps are resolved.

    Rank unresolved gaps, skip questions already resolved by the conversation, and
    keep each clarification thread focused on one feature with a three-question cap.
    Jev chooses among eligible gaps when confident; the BA model phrases the selected
    gap using the feature details, recent messages, and answers already given.
    """
    ambiguities = detect_ambiguities(nodes)
    ranked = rank_gaps(ambiguities, nodes, edges)

    # Read the question and conversation history in a few project-wide queries, not per gap:
    # after multi-file uploads there may be hundreds of gaps, and per-gap round-trips took ~418s.
    asked_by_key: Dict[str, List[Any]] = {}
    asked_facts = await get_facts(ctx, session, predicate="asked")
    for fact in asked_facts:
        asked_by_key.setdefault(fact.subject_key, []).append(fact)
    answered_facts = await get_facts(ctx, session, predicate="answered")
    answered_by_key = {fact.subject_key: fact for fact in answered_facts}
    message_facts = await get_facts(ctx, session, predicate="message")
    message_facts = [
        fact for fact in message_facts
        if fact.subject_type == "ConversationTurn" and (fact.value or {}).get("text")
    ]
    all_user_messages = [str(fact.value.get("text", ""))[:800] for fact in message_facts]
    recent_messages = all_user_messages[-8:]

    all_previous_answers = []
    for key, answer_fact in answered_by_key.items():
        questions = asked_by_key.get(key, [])
        question = (questions[-1].value or {}).get("question") if questions else ""
        answer = (answer_fact.value or {}).get("answer", "")
        if answer:
            all_previous_answers.append({
                "question": str(question or ""),
                "answer": str(answer),
                "_seq": answer_fact.seq,
            })
    all_previous_answers.sort(key=lambda item: item["_seq"])
    previous_answers = all_previous_answers[-5:]
    previous_answers = [
        {"question": item["question"], "answer": item["answer"]}
        for item in previous_answers
    ]
    conversation_context = {
        "recent_messages": recent_messages,
        "previous_answers": previous_answers,
    }
    feature_thread_start_seq = max((
        fact.seq for fact in message_facts
        if _starts_new_feature_thread(str((fact.value or {}).get("text", "")))
    ), default=0)
    thread_asked_facts = [fact for fact in asked_facts if fact.seq > feature_thread_start_seq]
    thread_question_count = len(thread_asked_facts)
    if thread_question_count > MAX_CLARIFICATIONS_PER_THREAD:
        # Older projects may already have more than the new budget on the active
        # feature. Do not resurrect the excess pending question after deployment.
        return None

    latest_message_seq = max((fact.seq for fact in message_facts), default=0)
    recent_questions = [
        str((fact.value or {}).get("question", ""))
        for fact in thread_asked_facts[-MAX_CLARIFICATIONS_PER_THREAD:]
        if (fact.value or {}).get("question")
    ]
    conversation_context["recent_questions"] = recent_questions
    stakeholders_clear = _stakeholders_already_clear(all_user_messages, all_previous_answers)

    eligible: List[Dict[str, Any]] = []
    for gap in ranked:
        node_id = gap.get("node_id") or ""
        field = gap.get("field") or ""
        subj_key = gap_subject_key(node_id, field)

        asked_facts = asked_by_key.get(subj_key, [])
        has_answer = subj_key in answered_by_key

        if asked_facts and not has_answer:
            latest_asked = asked_facts[-1]
            if latest_message_seq <= latest_asked.seq:
                question = latest_asked.value or {}
                return {
                    "gap_key": subj_key,
                    "node_id": node_id,
                    "field": field,
                    "node_type": gap.get("node_type"),
                    "question": question.get("question"),
                    "score": gap.get("_score", 0.0),
                    "resumed": True,
                }
            # A newer free-form message supersedes the old pending question.
            # Let the conversation context guide a fresh question instead.
            continue

        if has_answer:
            continue

        if thread_question_count >= MAX_CLARIFICATIONS_PER_THREAD:
            continue

        field_name = str(field).lower()
        is_stakeholder_gap = any(token in field_name for token in ("stakeholder", "approver", "decision_maker", "owner"))
        if stakeholders_clear and is_stakeholder_gap:
            continue
        eligible.append(gap)

    if not eligible:
        return None

    # When the user has described a concrete requirement, clarify that feature
    # before asking the generic project-discovery questions.
    feature_gaps = [gap for gap in eligible if gap.get("node_type") != "ProjectDiscovery"]
    if feature_gaps:
        eligible = feature_gaps

    # Keep a clarification thread focused on one feature or project-level topic.
    active_node_id = eligible[0].get("node_id")
    eligible = [gap for gap in eligible if gap.get("node_id") == active_node_id][:3]
    node_by_id = {str(node.get("id")): node for node in nodes}
    selected_node_context = node_by_id.get(str(active_node_id), {})
    discovery_node = node_by_id.get("project-discovery", {})
    selected_node_context = {
        **selected_node_context,
        "project_context": discovery_node.get("attrs") or {},
    }

    selected_index = None
    if len(eligible) > 1:
        try:
            instructions = await fetch_prompt("9", "ba_jev_clarification_choice_v1")
            selected_index = await choose_clarification_gap(
                eligible, nodes, instructions, conversation_context=conversation_context,
            )
        except Exception as exc:
            logger.warning("BA_JEV_CLARIFICATION_FALLBACK reason=%s", type(exc).__name__)

    gap = eligible[selected_index] if selected_index is not None else eligible[0]
    node_id = gap.get("node_id") or ""
    field = gap.get("field") or ""
    subj_key = gap_subject_key(node_id, field)
    question_text = await phrase_clarification_question(
        gap,
        session_id=ctx.project_id,
        node_context=selected_node_context,
        recent_messages=recent_messages,
        recent_questions=recent_questions,
        previous_answers=previous_answers,
    )

    system_source = await _get_or_create_system_source(ctx, session)
    await assert_fact(
        ctx, session,
        subject_type="ClarificationQuestion",
        subject_key=subj_key,
        predicate="asked",
        source_id=system_source.id,
        asserted_by="ba_clarification_engine",
        value={"question": question_text, "node_id": node_id, "field": field},
    )

    return {
        "gap_key": subj_key,
        "node_id": node_id,
        "field": field,
        "node_type": gap.get("node_type"),
        "question": question_text,
        "score": gap.get("_score", 0.0),
    }



# ---------------------------------------------------------------------------
# Answer recording + conflict detection
# ---------------------------------------------------------------------------

@traced("record-clarification-answer", tags=["business-analyst", "clarification"], input=lambda args: {"question": args.get("gap_key"), "answer": args.get("answer")})
async def record_clarification_answer(
    ctx: BATenantContext,
    session: AsyncSession,
    gap_key: str,
    answer: str,
) -> Dict[str, Any]:
    """Records the stakeholder's answer and checks for conflicts with prior answers.

    Returns: { "answer": str, "conflict_notice": str | None }
    """
    # Load prior answered facts for this gap
    prior_answered = await get_facts(ctx, session, subject_key=gap_key, predicate="answered")
    prior_dicts = [
        {"subject_key": f.subject_key, "value": f.value}
        for f in prior_answered
    ]

    # Deterministic conflict detection (no LLM)
    conflict = detect_value_conflict(answer, prior_dicts)
    conflict_notice: Optional[str] = None
    if conflict:
        conflict_notice = await phrase_conflict_notice(conflict)

    # Record the answer regardless — append-only; old answers remain in history
    system_source = await _get_or_create_system_source(ctx, session)
    await assert_fact(
        ctx, session,
        subject_type="ClarificationQuestion",
        subject_key=gap_key,
        predicate="answered",
        source_id=system_source.id,
        asserted_by="ba_clarification_engine",
        value={"answer": answer},
    )

    return {"answer": answer, "conflict_notice": conflict_notice}
