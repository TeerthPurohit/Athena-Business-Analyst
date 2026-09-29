"""FastAPI routes for BA OS (§11, Phase 11).

CLAUDE.md Non-Negotiables:
- ba_router mounted with prefix="/api/ba", tags=["Business Analyst OS"].
- ba_projects_router mounted with prefix="/api/ba/projects" — NO project_id dep,
  uses get_ba_org_context for project create/list routes.
- get_ba_tenant_context mounted ONCE on ba_router with a live AsyncSession dependency —
  DB validation CANNOT be bypassed.
- Reject header overrides, sub/workspaceId. Foreign project -> 404 Not Found.
- Facts approval: POST /api/ba/projects/{project_id}/facts/{fact_id}/approve (distinct verb).
- Deliverables approval: POST /api/ba/projects/{project_id}/deliverables/{instance_id}/approve (distinct verb).
- Generate deliverable: POST /api/ba/projects/{project_id}/deliverables/{key}/generate.
"""
import asyncio
import hashlib
import logging
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.api.security import (
    get_ba_org_context,
    get_ba_tenant_context,
    get_ba_tenant_context_from_token,
)
from agents.business_analyst.clarification import (
    PROJECT_DISCOVERY_QUESTIONS,
    gap_subject_key,
    get_next_clarification,
    record_clarification_answer,
)
from agents.business_analyst.jev_client import decide_chat_turn
from agents.business_analyst.llm_client import get_structured_output
from agents.business_analyst.deliverables import (
    XLSX_DELIVERABLES,
    approve_deliverable_instance,
    is_stale,
    seed_deliverable_specs,
)
from agents.business_analyst.capabilities.projection import RenderedArtifact, render_deliverable
from agents.business_analyst.capabilities.projection.requirements_register import xlsx_preview
from agents.business_analyst.deliverables_pdf import PDF_DELIVERABLES, bounded_markdown_to_pdf, pdf_to_text
from agents.business_analyst.capabilities.projection.requirement_package import (
    render_requirement_package,
)

from agents.business_analyst.document_text import extract_text
from agents.business_analyst.business_context import ENTITY_KEY_FIELDS, is_empty
from agents.business_analyst.extraction import (
    analyze_facts,
    analyze_requirements,
    extract_and_persist_facts,
    persist_facts,
    persist_requirements,
)
from agents.business_analyst.facts import (
    BATenantContext,
    approve_fact,
    assert_fact,
    get_facts,
    register_source,
)
from agents.business_analyst.ir import ProjectIR
from agents.business_analyst.jev_client import JEV_EVIDENCE_CHARS, JevDecisionError
from agents.business_analyst.observability import observation, traced
from agents.business_analyst.models import (
    BaDeliverableInstance,
    BaDeliverableSpec,
    BaEdge,
    BaFact,
    BaNode,
    BaProject,
    BaRun,
    BaSource,
)
from agents.business_analyst.project_summary import regenerate_project_summary
from agents.business_analyst.project_deletion import delete_ba_project
from agents.business_analyst.project_harness import run_project_analysis
from agents.business_analyst.registry import get_industry_template
from athena.config import Settings
from athena.storage import ObjectStore, S3ObjectStore
from agents.business_analyst.semantic_planner import build_project_ir_llm
from models.engine import get_async_session


async def get_db() -> AsyncSession:
    """FastAPI AsyncSession dependency."""
    async with get_async_session() as session:
        yield session


def get_deliverable_store() -> ObjectStore:
    """Resolve object storage only when a deliverable is generated or opened."""
    settings = Settings()
    values = (
        settings.object_storage_endpoint,
        settings.object_storage_access_key,
        settings.object_storage_secret_key,
        settings.object_storage_bucket,
        settings.object_storage_region,
    )
    if not all(values):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Deliverable object storage is not configured.",
        )
    return S3ObjectStore(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        bucket=settings.object_storage_bucket,
        region=settings.object_storage_region,
        force_path_style=settings.object_storage_force_path_style,
    )


async def get_ba_tenant_deps(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BATenantContext:
    """Mounted router-level dependency ensuring JWT tenant validation and BaProject DB lookup."""
    return await get_ba_tenant_context(request, session=db)


async def get_ba_org_deps(request: Request) -> str:
    """Router-level dependency for project-less routes — JWT/org only, no BaProject lookup."""
    return await get_ba_org_context(request)


# ---------------------------------------------------------------------------
# Router with project_id requirement (existing — unchanged prefix)
# ---------------------------------------------------------------------------

ba_router = APIRouter(
    prefix="/api/ba",
    tags=["Business Analyst OS"],
    dependencies=[Depends(get_ba_tenant_deps)],
)


# ---------------------------------------------------------------------------
# Router WITHOUT project_id — for project create / list
# ---------------------------------------------------------------------------

ba_projects_router = APIRouter(
    prefix="/api/ba/projects",
    tags=["Business Analyst OS"],
)


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------

class SemanticPlannerRequest(BaseModel):
    user_request: str
    session_id: Optional[str] = None


class FactApproveRequest(BaseModel):
    approved_by: str = "human_reviewer"


class DeliverableApproveRequest(BaseModel):
    approved_by: str = "stakeholder"


class RunStartRequest(BaseModel):
    objective: str


class ProjectCreateRequest(BaseModel):
    name: str
    instructions: Optional[str] = None
    must_have: Optional[str] = None
    should_have: Optional[str] = None
    industry_template_key: Optional[str] = None  # Feature 2 hook


class ProjectPatchRequest(BaseModel):
    name: Optional[str] = None
    instructions: Optional[str] = None
    must_have: Optional[str] = None
    should_have: Optional[str] = None
    project_summary: Optional[Dict[str, Any]] = None  # direct user edits
    scope_approved: Optional[bool] = None


class ClarificationAnswerRequest(BaseModel):
    answer: str
    next_step: Optional[str] = None


class ChatMessageRequest(BaseModel):
    message: str
    pending_question: Optional[str] = None
    recent_turns: List[Dict[str, str]] = Field(default_factory=list)
    tone: Optional[str] = None


class GreetingReply(BaseModel):
    reply: str


async def _greeting_reply(message: str, recent_turns: List[Dict[str, str]]) -> str:
    """Use the chat model for a short social reply without running fact extraction."""
    context = [{"user": turn.get("user", "")[:300], "assistant": turn.get("assistant", "")[:300]}
               for turn in recent_turns[-2:]]
    try:
        result = await asyncio.wait_for(
            get_structured_output(
                "You are Athena, a helpful business analyst. Reply naturally to a brief greeting in one short sentence. Invite the user to share what they need. Do not claim to have inspected or changed the project.",
                json.dumps({"message": message[:300], "recent_turns": context}, ensure_ascii=False),
                GreetingReply,
                max_retries=1,
                max_tokens=100,
                name="greet-project-user",
            ),
            timeout=8,
        )
        reply = result.reply.strip()
        if reply:
            return reply[:400]
    except Exception:
        logger.exception("BA greeting generation failed")
    return "Hi! What would you like to work through?"


class VoiceSpeechRequest(BaseModel):
    text: str


@ba_router.post("/projects/{project_id}/chat/intent")
async def classify_chat_intent_endpoint(
    project_id: str,
    req: ChatMessageRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty.")
    specs = (await db.execute(select(BaDeliverableSpec).where(
        (BaDeliverableSpec.org_id.is_(None)) | (BaDeliverableSpec.org_id == ctx.org_id)
    ))).scalars().all()
    options = {spec.key: f"{spec.key.replace('_', ' ')} ({spec.purpose})" for spec in specs}
    with observation(
        "route-chat-message", input=req.message, session_id=ctx.project_id,
        tags=["business-analyst", "chat"], metadata={"org_id": ctx.org_id},
    ) as obs:
        decision = await decide_chat_turn(req.message, options, req.pending_question, req.recent_turns)
        obs.update(output={"action": decision.action, "tone": decision.tone, "topic": decision.topic, "next_step": decision.next_step})
    selected = decision.action
    if selected is None and re.fullmatch(r"(?:hi|hello|hey|good morning|good afternoon|good evening)[.! ]*", req.message.strip(), re.IGNORECASE):
        selected = "greeting"
    guidance = {"tone": decision.tone, "topic": decision.topic, "next_step": decision.next_step}
    if selected == "greeting":
        reply = await _greeting_reply(req.message, req.recent_turns)
        source = await _get_or_create_chat_source(ctx, db)
        await assert_fact(
            ctx, db, subject_type="ConversationTurn", subject_key=str(uuid.uuid4()),
            predicate="message", source_id=source.id, asserted_by="project_user",
            value={"text": req.message, "reply": reply, "steps": ["greet_project_user"], "facts_created": 0},
        )
        await db.commit()
        return {"action": "greeting", "reply": reply, **guidance}
    if selected and selected.startswith("deliverable_"):
        key = selected.removeprefix("deliverable_")
        if key in options:
            return {"action": "deliverable", "deliverable_key": key, **guidance}
    if selected in {"record", "investigate", "answer"}:
        return {"action": selected, **guidance}
    if req.message.strip().endswith("?"):
        return {"action": "investigate", **guidance}
    return {"action": "answer" if req.pending_question and decision.topic != "changed" else "record", **guidance}


class ProjectAnalyzeRequest(BaseModel):
    question: str
    tone: Optional[str] = None
    next_step: Optional[str] = None


# ---------------------------------------------------------------------------
# ba_projects_router: POST /api/ba/projects, GET /api/ba/projects
# ---------------------------------------------------------------------------

@ba_projects_router.post("", status_code=status.HTTP_201_CREATED)
async def create_project_endpoint(
    req: ProjectCreateRequest,
    org_id: str = Depends(get_ba_org_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Creates a new BA project for the caller's org, optionally applying an industry template."""
    # Feature 2: resolve template defaults — explicit body fields always win
    instructions = req.instructions
    must_have = req.must_have
    should_have = req.should_have

    if req.industry_template_key:
        tpl = await get_industry_template(db, req.industry_template_key, org_id=org_id)
        if not tpl:
            # Try global fallback
            tpl = await get_industry_template(db, req.industry_template_key)
        if tpl:
            if instructions is None:
                instructions = tpl.default_instructions
            if must_have is None:
                must_have = tpl.default_must_have
            if should_have is None:
                should_have = tpl.default_should_have

    settings: Dict[str, Any] = {}
    if instructions is not None:
        settings["instructions"] = instructions
    if must_have is not None:
        settings["must_have"] = must_have
    if should_have is not None:
        settings["should_have"] = should_have

    project = BaProject(
        id=str(uuid.uuid4()),
        org_id=org_id,
        name=req.name,
        settings=settings,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    return {
        "id": project.id,
        "org_id": project.org_id,
        "name": project.name,
        "settings": project.settings,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


@ba_projects_router.get("")
async def list_projects_endpoint(
    org_id: str = Depends(get_ba_org_deps),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Lists all BA projects for the caller's org."""
    stmt = select(BaProject).where(BaProject.org_id == org_id).order_by(BaProject.created_at.desc())
    res = await db.execute(stmt)
    projects = res.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "settings": p.settings,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
            "summary_updated_at": p.summary_updated_at.isoformat() if p.summary_updated_at else None,
        }
        for p in projects
    ]


# ---------------------------------------------------------------------------
# ba_router: PATCH  /api/ba/projects/{project_id}
#            DELETE /api/ba/projects/{project_id}
#            POST   /api/ba/projects/{project_id}/summary/regenerate

@ba_router.patch("/projects/{project_id}")
async def patch_project_endpoint(
    project_id: str,
    req: ProjectPatchRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Partial update of project name and/or settings sub-fields.

    Supports direct user edits to project_summary — same write path as AI regeneration.
    Merges only supplied fields; unmentioned settings sub-fields are preserved.
    """
    project = await db.get(BaProject, project_id)
    if project is None or project.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")

    if req.name is not None:
        project.name = req.name

    # Merge settings — preserve existing sub-fields not mentioned in request
    new_settings = dict(project.settings or {})
    if req.instructions is not None:
        new_settings["instructions"] = req.instructions
    if req.must_have is not None:
        new_settings["must_have"] = req.must_have
    if req.should_have is not None:
        new_settings["should_have"] = req.should_have
    if req.project_summary is not None:
        new_settings["project_summary"] = req.project_summary
        new_settings["scope_approved"] = False
    if req.scope_approved is not None:
        if req.scope_approved and not new_settings.get("project_summary"):
            raise HTTPException(status_code=400, detail="Create a project scope before approving it.")
        new_settings["scope_approved"] = req.scope_approved
    project.settings = new_settings
    project.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(project)

    return {
        "id": project.id,
        "name": project.name,
        "settings": project.settings,
        "updated_at": project.updated_at.isoformat(),
        "summary_updated_at": project.summary_updated_at.isoformat() if project.summary_updated_at else None,
    }


@ba_router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_endpoint(
    project_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Deletes an entire BA project and all associated data, artifacts, and files.

    Returns 204 No Content on success.
    Foreign or missing project -> 404 Not Found (enforced by get_ba_tenant_deps).
    """
    store: Optional[ObjectStore] = None
    try:
        store = get_deliverable_store()
    except HTTPException:
        store = None

    await delete_ba_project(ctx=ctx, db=db, store=store)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@ba_router.post("/projects/{project_id}/summary/regenerate")
async def regenerate_project_summary_endpoint(
    project_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Triggers AI regeneration of project_summary from current BaFact graph.

    On LLM failure: returns existing summary unchanged (BA_PROJECT_SUMMARY_DEGRADED logged).
    """
    project = await db.get(BaProject, project_id)
    if project is None or project.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")

    summary = await regenerate_project_summary(ctx, db, project)
    await db.commit()

    return {
        "project_id": project_id,
        "project_summary": summary,
        "summary_updated_at": project.summary_updated_at.isoformat() if project.summary_updated_at else None,
    }


# ---------------------------------------------------------------------------
# Existing ba_router routes (unchanged, except approve_fact bug fix on line below)
# ---------------------------------------------------------------------------

@ba_router.post("/planner/semantic", response_model=ProjectIR)
async def semantic_planner_endpoint(
    req: SemanticPlannerRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
) -> ProjectIR:
    """Parses natural language request into a ProjectIR object using agent_id='9' prompt."""
    try:
        return await build_project_ir_llm(req.user_request, session_id=req.session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@ba_router.get("/projects/{project_id}/facts")
async def get_project_facts_endpoint(
    project_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Returns current active facts for a project (walking replacement chains)."""
    facts = await get_facts(ctx, db)
    return [
        {
            "id": f.id,
            "project_id": f.project_id,
            "org_id": f.org_id,
            "subject_type": f.subject_type,
            "subject_key": f.subject_key,
            "predicate": f.predicate,
            "value": f.value,
            "object_type": f.object_type,
            "object_key": f.object_key,
            "source_id": f.source_id,
            "human_approval": f.human_approval,
            "seq": f.seq,
        }
        for f in facts
    ]


@ba_router.post("/projects/{project_id}/facts/{fact_id}/approve")
async def approve_fact_endpoint(
    project_id: str,
    fact_id: str,
    req: FactApproveRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Distinct approval verb endpoint setting human_approval=True on a fact copy."""
    # BUG FIX: was approved_by=req.approved_by — facts.py uses asserted_by keyword
    try:
        approved = await approve_fact(ctx, db, fact_id=fact_id, asserted_by=req.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fact not found or already superseded.") from exc
    return {"status": "approved", "approved_fact_id": approved.id, "subject_key": approved.subject_key}


# ponytail: files land on local disk, not shared object storage — fine for a single API
# process, breaks once worker-scrape/worker-llm containers need to read them. Upgrade path:
# swap this for agents/content_images/storage.py's MinIO client when a real extraction
# pipeline needs to read uploaded sources from another container.
UPLOAD_DIR = Path(__file__).resolve().parents[3] / "data" / "ba_uploads"
# Chunks never exceed what the finding judge reads, or tail findings are rejected unseen.
ANALYSIS_CHUNK_CHARS = JEV_EVIDENCE_CHARS
# Chunks analyzed at once across all files of an upload (each runs two LLM pipelines).
INGEST_CONCURRENCY = int(os.getenv("BA_INGEST_CONCURRENCY", "4"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_BATCH_FILES = 20
SSE_KEEPALIVE_SECONDS = 15
VALIDATION_UNAVAILABLE = "Finding validation is unavailable. No information was recorded; try again later."
logger = logging.getLogger(__name__)


def _source_chunks(text: str) -> List[str]:
    """Keep each model request bounded while covering the entire extracted source."""
    chunks: List[str] = []
    position = 0
    while position < len(text):
        end = min(position + ANALYSIS_CHUNK_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind("\n", position + ANALYSIS_CHUNK_CHARS // 2, end)
            if boundary > position:
                end = boundary + 1
        chunks.append(text[position:end])
        position = end
    return chunks


Emit = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


def _join_words(parts: List[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _count(count: int, singular: str, plural: Optional[str] = None) -> Optional[str]:
    return f"{count} {singular if count == 1 else plural or singular + 's'}" if count else None


def _ir_highlights(ir: ProjectIR) -> List[str]:
    """Plain-language summary of what a judged IR adds to the project (no internal terms)."""
    context = ir.business_context or {}
    stakeholders = [str(record["name"]) for record in context.get("stakeholders", []) if isinstance(record, dict) and record.get("name")]
    context_details = sum(
        len(value) if section in ENTITY_KEY_FIELDS else sum(not is_empty(item) for item in value.values())
        for section, value in context.items() if isinstance(value, (list, dict))
    )
    parts = [
        _count(len(ir.objectives) + len(ir.goals), "goal"),
        _count(len(ir.scope.in_scope) + len(ir.scope.out_of_scope), "scope item"),
        _count(len(ir.scope.constraints), "constraint"),
        _count(len(ir.decisions), "decision"),
        _count(len(ir.business_rules), "business rule"),
        _count(len(ir.assumptions), "assumption"),
        _count(len(ir.open_questions), "open question"),
        _count(context_details, "business context detail"),
        f"stakeholders: {', '.join(stakeholders[:5])}" if stakeholders else None,
    ]
    return [part for part in parts if part]


def _record_reply(result: Dict[str, Any], tone: str | None = None) -> str:
    """The chat reply after recording a message — built from what was actually recorded."""
    if tone == "frustrated":
        return ("I hear you. I’ve added this to the project and will focus on the concern." if result.get("facts_created")
                else "I hear you. I couldn’t confirm a new project fact from that message.")
    if not result.get("facts_created"):
        # A free-form clarification may add useful context before the extractor can
        # turn it into a structured fact. The next question should carry the turn.
        return "I’ve got it. That context will help me shape the feature."
    return "Got it. I’ve added that to the project."


def _describe_part(outcomes: List[Any]) -> str:
    requirements, ir = outcomes
    found: List[str] = []
    if not isinstance(requirements, BaseException) and requirements.requirements:
        examples = [req.task or req.object for req in requirements.requirements if req.task or req.object][:2]
        label = _count(len(requirements.requirements), "requirement")
        found.append(f"{label} (for example: {'; '.join(examples)})" if examples else label)
    if not isinstance(ir, BaseException):
        found.extend(_ir_highlights(ir))
    failed = sum(isinstance(outcome, BaseException) for outcome in outcomes)
    note = " Part of the analysis could not finish." if failed else ""
    return (f"Found {_join_words(found)}." if found else "Nothing new could be confirmed in this part.") + note


@traced(
    "analyze-documents", tags=["business-analyst", "documents"],
    input=lambda args: [source.ref for source, _ in args["documents"]],
    output=lambda results: {source_id: {k: v for k, v in result.items() if k != "issues"} for source_id, result in results.items()},
)
async def _analyze_source_texts(
    ctx: BATenantContext,
    db: AsyncSession,
    documents: List[Tuple[BaSource, str]],
    emit: Emit = None,
) -> Dict[str, Dict[str, Any]]:
    """Analyzes every part of every document concurrently and records each part as it finishes.

    The LLM + judge work (analyze_*) touches no DB, so up to INGEST_CONCURRENCY parts run at
    once across all files. Writes (persist_*) share this one session behind a lock — the pool is
    too small for a session per part — each on its own savepoint. A source is marked analyzed
    only when every part succeeded. Each finished part gets its own marker (keyed by its character
    range), so a retry analyzes only the parts that failed instead of re-recording the rest.
    """
    limit = asyncio.Semaphore(INGEST_CONCURRENCY)
    db_lock = asyncio.Lock()
    results = {
        source.id: {"requirements_created": 0, "gaps_created": 0, "facts_created": 0, "chunks_total": 0, "chunks_analyzed": 0, "issues": []}
        for source, _ in documents
    }
    steps = (
        ("requirement", persist_requirements, "ba_requirement_extractor"),
        ("project context", persist_facts, "ba_document_analysis"),
    )

    async def run_part(source: BaSource, index: int, offset: int, chunk: str, parts: int) -> None:
        where = f"{source.ref} (part {index + 1} of {parts})" if parts > 1 else source.ref
        part_key = _part_key(source.id, offset, chunk)
        if part_key in finished_parts:
            results[source.id]["chunks_analyzed"] += 1
            if emit:
                await emit({"type": "tool_result", "tool": "analyze_source", "file": source.ref, "part": index + 1, "parts": parts, "message": f"{where}: already analyzed"})
            return
        with observation("analyze-document-part", input={"file": source.ref, "part": index + 1, "parts": parts}, metadata={"source_id": source.id}, label=f"analyze-document-part [{where}]") as part_obs:
            await _run_part(source, index, offset, chunk, parts, where, part_obs, part_key)

    async def _run_part(source: BaSource, index: int, offset: int, chunk: str, parts: int, where: str, part_obs: Any, part_key: str) -> None:
        async with limit:
            if emit:
                await emit({"type": "tool_call", "tool": "analyze_source", "specialist": "Document analyst", "file": source.ref, "part": index + 1, "parts": parts, "message": f"Reading {where}"})
            outcomes = await asyncio.gather(
                analyze_requirements(chunk, chunk_index=index, start_offset=offset),
                analyze_facts(chunk),
                return_exceptions=True,
            )
        result = results[source.id]
        part_ok = True
        async with db_lock:
            for (label, persist, asserted_by), outcome in zip(steps, outcomes):
                try:
                    if isinstance(outcome, BaseException):
                        raise outcome
                    async with db.begin_nested():
                        written = await persist(ctx, db, outcome, source_id=source.id, asserted_by=asserted_by)
                except Exception:
                    part_ok = False
                    logger.exception("BA source %s analysis failed source_id=%s part=%s", label, source.id, index + 1)
                    result["issues"].append(f"Part {index + 1}: {label} analysis could not finish.")
                    continue
                for key in ("requirements_created", "gaps_created", "facts_created"):
                    result[key] += written.get(key, 0)
            if part_ok:
                result["chunks_analyzed"] += 1
                await assert_fact(
                    ctx, db, subject_type="SourceAnalysis", subject_key=part_key,
                    predicate="part_completed", source_id=source.id,
                    asserted_by="ba_document_analysis", value={"part": index + 1, "parts": parts},
                )
        described = _describe_part(outcomes)
        part_obs.update(output=described, level="WARNING" if not part_ok else None)
        if emit:
            await emit({"type": "tool_result", "tool": "analyze_source", "file": source.ref, "part": index + 1, "parts": parts, "message": f"{where}: {described}"})

    finished_parts = set((await db.execute(select(BaFact.subject_key).where(
        BaFact.org_id == ctx.org_id,
        BaFact.project_id == ctx.project_id,
        BaFact.subject_type == "SourceAnalysis",
        BaFact.predicate == "part_completed",
        BaFact.source_id.in_([source.id for source, _ in documents]),
    ))).scalars().all()) if documents else set()

    jobs = []
    for source, text in documents:
        chunks = _source_chunks(text)
        results[source.id]["chunks_total"] = len(chunks)
        offset = 0
        for index, chunk in enumerate(chunks):
            jobs.append(run_part(source, index, offset, chunk, len(chunks)))
            offset += len(chunk)
    await asyncio.gather(*jobs)

    for source, _ in documents:
        result = results[source.id]
        if result["chunks_total"] and result["chunks_analyzed"] == result["chunks_total"]:
            await assert_fact(
                ctx, db, subject_type="SourceAnalysis", subject_key=source.id,
                predicate="completed", source_id=source.id,
                asserted_by="ba_document_analysis", value={"stage": "semantic"},
            )
        else:
            await assert_fact(
                ctx, db, subject_type="Gap", subject_key=f"source-extraction:{source.id}",
                predicate="extraction_failure", source_id=source.id,
                asserted_by="ba_requirement_extractor",
                value={"source_id": source.id, "reason": "partial_source_analysis"},
            )
    return results


def _part_key(source_id: str, offset: int, chunk: str) -> str:
    """A part's identity is its character range, so a change in chunk size never matches old markers."""
    return f"{source_id}:{offset}-{offset + len(chunk)}"


def _extraction_issue(issues: List[str]) -> Optional[str]:
    if not issues:
        return None
    more = f" And {len(issues) - 5} more parts could not be analyzed." if len(issues) > 5 else ""
    return " ".join(issues[:5]) + more + " The file was saved; retry its analysis."


def _source_entry(source: BaSource, **extra: Any) -> Dict[str, Any]:
    return {
        "id": source.id,
        "kind": source.kind,
        "tier": source.tier,
        "ref": source.ref,
        "content_hash": source.content_hash,
        "captured_at": source.captured_at.isoformat(),
        **extra,
    }


async def _refresh_summary_if_changed(
    ctx: BATenantContext, db: AsyncSession, changed: bool, emit: Emit = None,
) -> Tuple[Optional[Dict[str, Any]], BaProject]:
    """Regenerates the summary only when new facts were recorded; new facts un-approve scope."""
    project = await db.get(BaProject, ctx.project_id)
    if not changed:
        return (project.settings or {}).get("project_summary"), project
    if emit:
        await emit({"type": "tool_call", "tool": "synthesize_project_scope", "specialist": "Scope analyst", "message": "Updating the project scope"})
    summary = await regenerate_project_summary(ctx, db, project)
    project.settings = {**(project.settings or {}), "scope_approved": False}
    if emit:
        await emit({"type": "tool_result", "tool": "synthesize_project_scope", "message": "Project scope updated"})
    return summary, project


async def _source_analyzed(ctx: BATenantContext, db: AsyncSession, source_id: str) -> bool:
    """True once every part of the source was analyzed (the marker _analyze_source_texts writes)."""
    return (await db.execute(select(BaFact.id).where(
        BaFact.source_id == source_id,
        BaFact.project_id == ctx.project_id,
        BaFact.org_id == ctx.org_id,
        BaFact.subject_type == "SourceAnalysis",
        BaFact.predicate == "completed",
    ).limit(1))).scalar_one_or_none() is not None


async def _read_uploads(uploads: List[UploadFile]) -> List[Tuple[str, bytes]]:
    """Reads uploads inside the request, before any streamed response outlives the form data."""
    return [(upload.filename or "upload", await upload.read(MAX_UPLOAD_BYTES + 1)) for upload in uploads]


@traced(
    "ingest-documents", tags=["business-analyst", "upload"],
    input=lambda args: [filename for filename, _ in args["files"]],
    output=lambda ingested: [{k: entry.get(k) for k in ("ref", "duplicate", "extraction", "extraction_issue", "error")} for entry in ingested["sources"]],
)
async def _ingest_uploads(
    ctx: BATenantContext, db: AsyncSession, files: List[Tuple[str, bytes]], emit: Emit = None,
) -> Dict[str, Any]:
    """Saves every file, analyzes all new documents concurrently, and refreshes the summary once."""
    entries: List[Dict[str, Any]] = []
    documents: List[Tuple[BaSource, str]] = []
    dest_dir = UPLOAD_DIR / ctx.org_id / ctx.project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in files:
        if not content:
            entries.append({"ref": filename, "error": "Uploaded file is empty."})
            continue
        if len(content) > MAX_UPLOAD_BYTES:
            entries.append({"ref": filename, "error": "File is larger than 25 MB."})
            continue
        content_hash = hashlib.sha256(content).hexdigest()
        existing = (await db.execute(select(BaSource).where(
            BaSource.org_id == ctx.org_id,
            BaSource.project_id == ctx.project_id,
            BaSource.kind == "document",
            BaSource.content_hash == content_hash,
        ).limit(1))).scalar_one_or_none()
        if existing is not None and await _source_analyzed(ctx, db, existing.id):
            # Re-analyzing identical content would duplicate every append-only fact it produced.
            entries.append(_source_entry(existing, duplicate=True, extraction=None, extraction_issue=None))
            if emit:
                await emit({"type": "status", "file": filename, "message": f"{filename} is already in this project, so it was not analyzed again"})
            continue
        if existing is not None:
            # Saved by an earlier upload whose analysis never completed: analyze it now instead
            # of skipping it as a duplicate.
            source = existing
        else:
            (dest_dir / f"{content_hash}_{Path(filename).name}").write_bytes(content)
            source = await register_source(ctx, db, kind="document", tier="tier1", content_hash=content_hash, ref=filename)
        entry = _source_entry(source, duplicate=False, extraction=None, extraction_issue=None)
        entries.append(entry)
        try:
            text = await asyncio.to_thread(extract_text, filename, content)
            if not text.strip():
                raise ValueError("No extractable text found in this file.")
            documents.append((source, text))
        except ValueError as exc:
            entry["extraction_issue"] = f"{exc} The file was saved."
        except Exception:
            logger.exception("BA source text extraction failed source_id=%s", source.id)
            entry["extraction_issue"] = "The file was saved, but its text could not be read."
        if emit and entry["extraction_issue"]:
            await emit({"type": "status", "file": filename, "message": f"{filename}: {entry['extraction_issue']}"})

    # Persist the files' provenance before the long analysis so the connection isn't held idle.
    await db.commit()
    # The commit expired every loaded row; reload the sources now, inside the async session.
    # Reading an expired attribute later (source.ref) would lazy-load synchronously and raise
    # MissingGreenlet, failing the whole batch.
    for source, _ in documents:
        await db.refresh(source)
    if emit and documents:
        await emit({"type": "status", "message": f"Analyzing {_count(len(documents), 'file')} at the same time"})
    results = await _analyze_source_texts(ctx, db, documents, emit)
    chat_source = await _get_or_create_chat_source(ctx, db)
    for entry in entries:
        result = results.get(entry.get("id"))
        if result is not None:
            entry["extraction_issue"] = _extraction_issue(result.pop("issues"))
            entry["extraction"] = result
        if entry.get("id") and not entry.get("duplicate"):
            await assert_fact(
                ctx, db, subject_type="ConversationTurn", subject_key=str(uuid.uuid4()),
                predicate="upload", source_id=chat_source.id, asserted_by="project_user",
                value={"filename": entry["ref"], "source_id": entry["id"]},
            )
    changed = any(any(result[key] for key in ("requirements_created", "gaps_created", "facts_created")) for result in results.values())
    summary, _ = await _refresh_summary_if_changed(ctx, db, changed, emit)
    await db.commit()
    return {"sources": entries, "project_summary": summary if results else None}


def _sse_response(work: Callable[[AsyncSession, Callable[[Dict[str, Any]], Awaitable[None]]], Awaitable[None]], failure: str) -> StreamingResponse:
    """Runs `work` in the background with its OWN session and relays its events as SSE.

    Own session: if the client disconnects, the task is cancelled and the session closes without
    committing, instead of the request session committing half a turn. Keepalive comments stop
    proxies from cutting the stream during long LLM calls; the client ignores non-data frames.
    """
    async def events():
        queue: asyncio.Queue[Dict[str, Any] | None] = asyncio.Queue()

        async def emit(event: Dict[str, Any]) -> None:
            await queue.put(event)

        async def run() -> None:
            try:
                async with get_async_session() as db:
                    await work(db, emit)
            except JevDecisionError:
                await emit({"type": "error", "message": VALIDATION_UNAVAILABLE})
            except Exception:
                logger.exception("BA stream failed")
                await emit({"type": "error", "message": failure})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), SSE_KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@ba_router.post("/projects/{project_id}/sources", status_code=status.HTTP_201_CREATED)
async def upload_source_endpoint(
    project_id: str,
    file: UploadFile = File(...),
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Uploads one source document and analyzes it. Kept for single-file clients; the streamed
    endpoint below is the same pipeline for many files at once with live progress."""
    ingested = await _ingest_uploads(ctx, db, await _read_uploads([file]))
    entry = ingested["sources"][0]
    if entry.get("error"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=entry["error"])
    return {**entry, "project_summary": ingested["project_summary"]}


@ba_router.post("/projects/{project_id}/sources/stream")
async def stream_upload_sources_endpoint(
    project_id: str,
    files: List[UploadFile] = File(...),
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Uploads up to MAX_BATCH_FILES documents, analyzes all their parts concurrently, and streams
    what is found in each file and part as it happens (SSE: status, tool_call, tool_result,
    result {sources, project_summary}, done | error)."""
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Add at least one file.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Upload at most {MAX_BATCH_FILES} files at once.")
    contents = await _read_uploads(files)
    await db.commit()  # release the tenant-lookup connection; the stream uses its own session

    async def work(session: AsyncSession, emit: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        ingested = await _ingest_uploads(ctx, session, contents, emit)
        await emit({"type": "result", "data": ingested})
        await emit({"type": "done"})

    return _sse_response(work, "The files could not be analyzed. Anything already saved is kept; please try again.")


@ba_router.get("/projects/{project_id}/sources")
async def list_sources_endpoint(
    project_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Lists source documents registered for a project, newest first."""
    stmt = (
        select(BaSource)
        .where(BaSource.project_id == ctx.project_id, BaSource.org_id == ctx.org_id)
        .order_by(BaSource.captured_at.desc())
    )
    res = await db.execute(stmt)
    sources = res.scalars().all()
    return [
        {
            "id": s.id,
            "kind": s.kind,
            "tier": s.tier,
            "ref": s.ref,
            "content_hash": s.content_hash,
            "captured_at": s.captured_at.isoformat(),
        }
        for s in sources
    ]


@ba_router.get("/projects/{project_id}/sources/{source_id}/download")
async def download_source_endpoint(
    project_id: str,
    source_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Response:
    source = await db.get(BaSource, source_id)
    if source is None or source.org_id != ctx.org_id or source.project_id != ctx.project_id or source.kind != "document":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    path = UPLOAD_DIR / ctx.org_id / ctx.project_id / f"{source.content_hash}_{Path(source.ref).name}"
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored source is missing.")
    from urllib.parse import quote
    return Response(
        content=path.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(Path(source.ref).name)}"},
    )


@ba_router.get("/projects/{project_id}/requirement-package")
async def get_requirement_package_endpoint(
    project_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Returns source-backed requirements and deterministic BA projections."""
    return await render_requirement_package(ctx, db)


async def _get_or_create_chat_source(ctx: BATenantContext, session: AsyncSession) -> BaSource:
    """Returns the project's chat-tier BaSource, creating it once if absent — sibling of
    clarification.py's _get_or_create_system_source, kept local since it's chat-specific
    provenance, not the clarification engine's."""
    stmt = select(BaSource).where(
        BaSource.project_id == ctx.project_id,
        BaSource.org_id == ctx.org_id,
        BaSource.kind == "chat",
    ).limit(1)
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing:
        return existing
    return await register_source(
        ctx, session,
        kind="chat", tier="tier1",
        content_hash=hashlib.sha256(f"chat:{ctx.project_id}".encode()).hexdigest(),
        ref="ba_chat",
    )


@traced("record-chat-message", tags=["business-analyst", "chat"], input=lambda args: args["text"], output=lambda result: result.get("reply"))
async def _extract_and_regenerate_summary(
    ctx: BATenantContext,
    db: AsyncSession,
    *,
    text: str,
    source_id: str,
    asserted_by: str,
    emit: Emit = None,
    tone: str | None = None,
) -> Dict[str, Any]:
    """Runs fact extraction, then regenerates project_summary in the same transaction — but only
    when the turn recorded something, so a turn with nothing new costs one pipeline, not two."""
    if emit:
        await emit({"type": "tool_call", "tool": "extract_project_facts", "specialist": "Context analyst", "message": "Reading your message"})
    result = await extract_and_persist_facts(ctx, db, text=text, source_id=source_id, asserted_by=asserted_by)
    if emit:
        highlights = _ir_highlights(result["ir"]) if result["facts_created"] else []
        await emit({"type": "tool_result", "tool": "extract_project_facts", "message": f"Confirmed {_join_words(highlights)}" if highlights else "Nothing new could be confirmed"})

    summary, project = await _refresh_summary_if_changed(ctx, db, bool(result["facts_created"]), emit)
    result["reply"] = _record_reply(result, tone)
    if asserted_by == "ba_chat_extraction":
        await assert_fact(
            ctx, db, subject_type="ConversationTurn", subject_key=str(uuid.uuid4()),
            predicate="message", source_id=source_id, asserted_by="project_user",
            value={"text": text, "reply": result["reply"], "steps": ["extract_project_facts", "synthesize_project_scope"], "facts_created": result.get("facts_created", 0)},
        )
    await db.commit()
    await db.refresh(project)

    result["project_summary"] = summary
    result["summary_updated_at"] = project.summary_updated_at.isoformat() if project.summary_updated_at else None
    return result


@ba_router.post("/projects/{project_id}/chat")
async def chat_message_endpoint(
    project_id: str,
    req: ChatMessageRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Extracts structured facts from a free-text chat message via the semantic planner
    and persists them to the append-only fact store. Real extraction — no mock reply."""
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message must not be empty.")

    source = await _get_or_create_chat_source(ctx, db)
    try:
        result = await _extract_and_regenerate_summary(
            ctx, db, text=req.message, source_id=source.id, asserted_by="ba_chat_extraction", tone=req.tone,
        )
    except JevDecisionError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=VALIDATION_UNAVAILABLE) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="I couldn't analyze that message just now. Nothing was recorded; please try again.",
        ) from exc

    return {
        "facts_created": result["facts_created"],
        "goal_facts": result["goal_facts"],
        "entity_facts": result["entity_facts"],
        "reply": result["reply"],
        "ir": result["ir"].model_dump(),
        "project_summary": result["project_summary"],
        "summary_updated_at": result["summary_updated_at"],
    }


@ba_router.post("/projects/{project_id}/chat/stream")
async def stream_chat_message_endpoint(
    project_id: str,
    req: ChatMessageRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty.")
    await db.commit()  # release the tenant-lookup connection; the stream uses its own session

    async def work(session: AsyncSession, emit: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        source = await _get_or_create_chat_source(ctx, session)
        result = await _extract_and_regenerate_summary(
            ctx, session, text=req.message, source_id=source.id,
            asserted_by="ba_chat_extraction", emit=emit, tone=req.tone,
        )
        await emit({"type": "result", "data": {
            "facts_created": result["facts_created"],
            "reply": result["reply"],
            "project_summary": result["project_summary"],
        }})
        await emit({"type": "done"})

    return _sse_response(work, "I couldn't analyze that message just now. Nothing was recorded; please try again.")


@ba_router.post("/projects/{project_id}/analyze")
async def analyze_project_endpoint(
    project_id: str,
    req: ProjectAnalyzeRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Investigate only the active project's facts and uploaded source documents."""
    if not req.question.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question must not be empty.")
    try:
        result = await run_project_analysis(ctx, db, req.question, tone=req.tone, escalate=req.next_step == "escalate")
    except RuntimeError as exc:
        logger.exception("BA project investigation unavailable")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Project investigation is unavailable right now. Please try again.") from exc
    source = await _get_or_create_chat_source(ctx, db)
    await assert_fact(
        ctx, db, subject_type="ConversationTurn", subject_key=str(uuid.uuid4()),
        predicate="investigation", source_id=source.id, asserted_by="ba_project_harness",
        value={"question": req.question, "answer": result.get("answer", ""), "steps": [item["tool"] for item in result.get("tools_used", [])], "evaluation": result.get("evaluation")},
    )
    await db.commit()
    return result


@ba_router.post("/projects/{project_id}/analyze/stream")
async def stream_project_analysis_endpoint(
    project_id: str,
    req: ProjectAnalyzeRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE: status/tool_call/tool_result steps, thinking_delta (model reasoning), answer_delta
    (answer tokens), answer (final text), done | error."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")
    await db.commit()  # release the tenant-lookup connection; the stream uses its own session

    async def work(session: AsyncSession, emit: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        result = await run_project_analysis(ctx, session, req.question, emit=emit, tone=req.tone, escalate=req.next_step == "escalate")
        source = await _get_or_create_chat_source(ctx, session)
        await assert_fact(
            ctx, session, subject_type="ConversationTurn", subject_key=str(uuid.uuid4()),
            predicate="investigation", source_id=source.id, asserted_by="ba_project_harness",
            value={"question": req.question, "answer": result.get("answer", ""), "steps": [item["tool"] for item in result.get("tools_used", [])], "evaluation": result.get("evaluation")},
        )
        await session.commit()
        await emit({"type": "done"})

    return _sse_response(work, "The project investigation could not finish. Please try again.")


@ba_router.post("/projects/{project_id}/sources/{source_id}/analyze")
async def analyze_source_endpoint(
    project_id: str,
    source_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Re-runs analysis of an already-uploaded source document (the retry for a failed or partial
    upload analysis), through the same chunked concurrent pipeline as upload."""
    source = await db.get(BaSource, source_id)
    if source is None or source.org_id != ctx.org_id or source.project_id != ctx.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    if not source.ref:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source has no stored file to analyze.")

    # Idempotency: a fully analyzed source is never re-analyzed. Append-only facts can't be
    # de-duped after the fact, so the guard has to run before the (expensive, real) LLM calls.
    # ponytail: a partially analyzed source re-runs every part, re-recording the parts that had
    # succeeded; per-part completion markers would fix that if partial failures become common.
    if await _source_analyzed(ctx, db, source_id):
        count_stmt = select(BaFact.id).where(BaFact.source_id == source_id)
        existing_count = len((await db.execute(count_stmt)).scalars().all())
        return {
            "facts_created": 0,
            "already_analyzed": True,
            "existing_fact_count": existing_count,
        }

    dest_path = UPLOAD_DIR / ctx.org_id / ctx.project_id / f"{source.content_hash}_{Path(source.ref).name}"
    if not dest_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file for this source is missing.")

    try:
        text = await asyncio.to_thread(extract_text, source.ref, dest_path.read_bytes())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if not text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No extractable text found in document.")

    result = (await _analyze_source_texts(ctx, db, [(source, text)]))[source.id]
    changed = any(result[key] for key in ("requirements_created", "gaps_created", "facts_created"))
    summary, project = await _refresh_summary_if_changed(ctx, db, changed)
    await db.commit()
    await db.refresh(project)

    return {
        "facts_created": result["facts_created"],
        "requirements_created": result["requirements_created"],
        "gaps_created": result["gaps_created"],
        "extraction_issue": _extraction_issue(result["issues"]),
        "already_analyzed": False,
        "project_summary": summary,
        "summary_updated_at": project.summary_updated_at.isoformat() if project.summary_updated_at else None,
    }


@ba_router.get("/projects/{project_id}/deliverables/catalog")
async def list_project_deliverable_catalog_endpoint(
    project_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List the deliverables available to this project, including tenant overrides."""
    stmt = select(BaDeliverableSpec).where(
        (BaDeliverableSpec.org_id.is_(None)) | (BaDeliverableSpec.org_id == ctx.org_id)
    )
    specs = (await db.execute(stmt)).scalars().all()
    by_key = {spec.key: spec for spec in specs if spec.org_id is None}
    by_key.update({spec.key: spec for spec in specs if spec.org_id == ctx.org_id})
    return [
        {
            "key": spec.key,
            "purpose": spec.purpose,
            "required_node_types": spec.required_node_types,
            "required_capabilities": spec.required_capabilities,
            "review_process": spec.review_process,
            "versioning_strategy": spec.versioning_strategy,
            "approval_workflow": spec.approval_workflow,
        }
        for spec in sorted(by_key.values(), key=lambda item: item.key)
    ]


@ba_router.get("/projects/{project_id}/deliverables")
async def list_project_deliverables_endpoint(
    project_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Lists deliverable instances for project with lazily-computed staleness."""
    stmt = select(BaDeliverableInstance).where(
        BaDeliverableInstance.project_id == ctx.project_id,
        BaDeliverableInstance.org_id == ctx.org_id,
    )
    res = await db.execute(stmt)
    instances = res.scalars().all()

    out = []
    for inst in instances:
        stale = await is_stale(inst, db)
        out.append(
            {
                "id": inst.id,
                "deliverable_key": inst.deliverable_key,
                "status": inst.status,
                "is_stale": stale,
                "frontier_seq": inst.frontier_seq,
                "output_format": "pdf" if inst.deliverable_key in PDF_DELIVERABLES else inst.output_format,
                "content_ref": inst.content_ref,
                "approved_by": inst.approved_by,
                "approved_at": inst.approved_at.isoformat() if inst.approved_at else None,
            }
        )
    return out


async def _load_project_deliverable(
    instance_id: str, ctx: BATenantContext, db: AsyncSession, store: ObjectStore
) -> tuple[BaDeliverableInstance, bytes]:
    stmt = select(BaDeliverableInstance).where(
        BaDeliverableInstance.id == instance_id,
        BaDeliverableInstance.project_id == ctx.project_id,
        BaDeliverableInstance.org_id == ctx.org_id,
    )
    instance = (await db.execute(stmt)).scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deliverable not found.")
    if instance.content_ref.startswith("inline://"):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This legacy deliverable was not stored. Generate it again to reopen or download it.",
        )
    content = await asyncio.to_thread(store.get, instance.content_ref)
    if instance.deliverable_key in PDF_DELIVERABLES and instance.output_format != "pdf":
        content = await asyncio.to_thread(markdown_to_pdf, content.decode("utf-8"))
    return instance, content


@ba_router.get("/projects/{project_id}/deliverables/{instance_id}")
async def get_project_deliverable_endpoint(
    project_id: str,
    instance_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
    store: ObjectStore = Depends(get_deliverable_store),
) -> Dict[str, Any]:
    """Read a generated deliverable within its project and organization."""
    instance, artifact = await _load_project_deliverable(instance_id, ctx, db, store)
    if instance.output_format == "pdf" or instance.deliverable_key in PDF_DELIVERABLES:
        preview_content = pdf_to_text(artifact)
        output_format = "pdf"
    elif instance.output_format == "xlsx" or instance.deliverable_key in XLSX_DELIVERABLES:
        preview_content = xlsx_preview(artifact)
        output_format = "xlsx"
    else:
        preview_content = artifact.decode("utf-8")
        output_format = instance.output_format
    return {
        "id": instance.id,
        "deliverable_key": instance.deliverable_key,
        "status": instance.status,
        "is_stale": await is_stale(instance, db),
        "output_format": output_format,
        "content": preview_content,
    }


@ba_router.post("/projects/{project_id}/chat/voice/transcriptions")
async def transcribe_chat_audio_endpoint(
    project_id: str,
    audio: UploadFile = File(...),
) -> Dict[str, Any]:
    """Transcribes a short browser voice recording through OpenRouter without storing it."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Voice input requires OPENROUTER_API_KEY to be configured.",
        )

    max_audio_bytes = 25 * 1024 * 1024
    audio_bytes = await audio.read(max_audio_bytes + 1)
    if not audio_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The recording is empty.")
    if len(audio_bytes) > max_audio_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Recordings must be 25 MB or smaller.")

    format_by_content_type = {
        "audio/aac": "aac",
        "audio/flac": "flac",
        "audio/mp4": "m4a",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/webm": "webm",
    }
    content_type = (audio.content_type or "").split(";", 1)[0].strip().lower()
    file_format = format_by_content_type.get(content_type)
    if not file_format:
        suffix = Path(audio.filename or "").suffix.lower().lstrip(".")
        file_format = suffix if suffix in {"aac", "flac", "m4a", "mp3", "ogg", "wav", "webm"} else None
    if not file_format:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Use a WebM, MP4, WAV, MP3, OGG, AAC, or FLAC recording.")

    model = os.getenv("BA_VOICE_STT_MODEL", "openai/gpt-transcribe")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=65.0,
        max_retries=0,
    )
    try:
        transcript = await client.audio.transcriptions.create(
            model=model,
            file=(f"voice-input.{file_format}", audio_bytes, audio.content_type or f"audio/{file_format}"),
        )
    except Exception as exc:  # noqa: BLE001 - upstream errors must not expose provider details to the client
        logger.exception("OpenRouter voice transcription failed project_id=%s", project_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Transcription failed. Try again or type your message.") from exc
    finally:
        await client.close()

    text = (transcript.text or "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No speech was detected. Try again or type your message.")
    return {"text": text, "model": model}


@ba_router.post("/projects/{project_id}/chat/voice/speech")
async def synthesize_chat_speech_endpoint(
    project_id: str,
    req: VoiceSpeechRequest,
) -> Response:
    """Returns an MP3 reading of an Athena reply; no generated audio is persisted."""
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="text must not be empty.")
    if len(text) > 12000:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Select a shorter reply to read aloud.")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Voice playback requires OPENROUTER_API_KEY to be configured.",
        )

    model = os.getenv("BA_VOICE_TTS_MODEL", "google/gemini-3.8-flash-lite-tts")
    voice = os.getenv("BA_VOICE_TTS_VOICE", "Kore")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=65.0,
        max_retries=0,
    )
    try:
        speech = await client.audio.speech.create(
            model=model,
            input=text,
            voice=voice,
            response_format="mp3",
        )
        audio_bytes = speech.content
    except Exception as exc:  # noqa: BLE001 - upstream errors must not expose provider details to the client
        logger.exception("OpenRouter voice synthesis failed project_id=%s model=%s", project_id, model)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Speech playback failed. Try again or read the reply.") from exc
    finally:
        await client.close()

    if not audio_bytes:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Speech playback returned no audio. Try again or read the reply.")
    return Response(content=audio_bytes, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@ba_router.get("/projects/{project_id}/deliverables/{instance_id}/download")
async def download_project_deliverable_endpoint(
    project_id: str,
    instance_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
    store: ObjectStore = Depends(get_deliverable_store),
) -> Response:
    """Download a generated deliverable in its stored format."""
    instance, artifact = await _load_project_deliverable(instance_id, ctx, db, store)
    is_pdf = instance.output_format == "pdf" or instance.deliverable_key in PDF_DELIVERABLES
    is_xlsx = instance.output_format == "xlsx" or instance.deliverable_key in XLSX_DELIVERABLES
    extension = "pdf" if is_pdf else "xlsx" if is_xlsx else "md"
    media_type = (
        "application/pdf" if is_pdf
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if is_xlsx
        else "text/markdown; charset=utf-8"
    )
    return Response(
        content=artifact,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{instance.deliverable_key}.{extension}"'},
    )


@ba_router.post("/projects/{project_id}/deliverables/{key}/generate")
async def generate_deliverable_endpoint(
    project_id: str,
    key: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
    store: ObjectStore = Depends(get_deliverable_store),
) -> Dict[str, Any]:
    """Generates a deliverable instance for the project."""
    stmt_spec = select(BaDeliverableSpec).where(
        BaDeliverableSpec.key == key,
        (BaDeliverableSpec.org_id.is_(None)) | (BaDeliverableSpec.org_id == ctx.org_id),
    )
    res_spec = await db.execute(stmt_spec)
    specs = res_spec.scalars().all()
    spec = next((item for item in specs if item.org_id == ctx.org_id), None)
    if spec is None:
        spec = next((item for item in specs if item.org_id is None), None)

    if not spec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deliverable specification '{key}' not found.",
        )

    # Get max frontier_seq across project facts
    stmt_facts = select(BaFact).where(
        BaFact.project_id == ctx.project_id,
        BaFact.org_id == ctx.org_id,
    )
    res_facts = await db.execute(stmt_facts)
    all_facts = res_facts.scalars().all()
    max_seq = max([f.seq for f in all_facts], default=0)

    with observation(
        "generate-deliverable", input={"deliverable": key}, session_id=ctx.project_id,
        tags=["business-analyst", "deliverable"], metadata={"org_id": ctx.org_id},
    ) as obs:
        rendered = await render_deliverable(spec, ctx, db)
        obs.update(output=rendered.preview if isinstance(rendered, RenderedArtifact) else rendered)

    if key in XLSX_DELIVERABLES and not isinstance(rendered, RenderedArtifact):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The Excel renderer for '{key}' is unavailable.",
        )
    if isinstance(rendered, RenderedArtifact):
        output_format = rendered.output_format
        artifact = rendered.content
        preview_content = rendered.preview
        extension = rendered.extension
        media_type = rendered.media_type
    else:
        content_str = rendered
        output_format = "pdf" if key in PDF_DELIVERABLES else "markdown"
        is_pdf = output_format == "pdf"
        artifact = await asyncio.to_thread(bounded_markdown_to_pdf, content_str, key) if is_pdf else content_str.encode("utf-8")
        preview_content = pdf_to_text(artifact) if is_pdf else content_str
        extension = "pdf" if is_pdf else "md"
        media_type = "application/pdf" if is_pdf else "text/markdown"
    instance_id = str(uuid.uuid4())
    instance = BaDeliverableInstance(
        id=instance_id,
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        deliverable_key=key,
        frontier_seq=max_seq,
        renderer_version="1.0.0",
        output_format=output_format,
        content_ref="",
        status="generated",
    )
    storage_key = f"deliverables/{ctx.org_id}/{ctx.project_id}/{instance_id}.{extension}"
    instance.content_ref = await asyncio.to_thread(
        store.put_if_absent, storage_key, artifact, media_type
    )
    db.add(instance)
    chat_source = await _get_or_create_chat_source(ctx, db)
    await assert_fact(
        ctx, db, subject_type="ConversationTurn", subject_key=str(uuid.uuid4()),
        predicate="deliverable", source_id=chat_source.id, asserted_by="ba_deliverable_agent",
        value={"deliverable_key": key, "instance_id": instance_id},
    )
    response_payload = {
        "id": instance_id,
        "deliverable_key": key,
        "status": "generated",
        "frontier_seq": max_seq,
        "output_format": output_format,
        "content": preview_content,
    }
    await db.commit()
    return response_payload


@ba_router.post("/projects/{project_id}/deliverables/{instance_id}/approve")
async def approve_deliverable_endpoint(
    project_id: str,
    instance_id: str,
    req: DeliverableApproveRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Distinct approval verb endpoint setting status='approved' on a deliverable instance."""
    approved_inst = await approve_deliverable_instance(instance_id, req.approved_by, ctx, db)
    return {
        "status": approved_inst.status,
        "instance_id": approved_inst.id,
        "approved_by": approved_inst.approved_by,
        "approved_at": approved_inst.approved_at.isoformat() if approved_inst.approved_at else None,
    }


@ba_router.post("/projects/{project_id}/runs")
async def start_run_endpoint(
    project_id: str,
    req: RunStartRequest,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Starts a new BA OS execution run."""
    run = BaRun(
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        status="running",
        planner_mode="deterministic",
    )
    db.add(run)
    await db.commit()
    return {"run_id": run.id, "status": run.status}


@ba_router.get("/projects/{project_id}/runs/{run_id}")
async def get_run_status_endpoint(
    project_id: str,
    run_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Gets execution status of a run."""
    stmt = select(BaRun).where(
        BaRun.id == run_id,
        BaRun.project_id == ctx.project_id,
        BaRun.org_id == ctx.org_id,
    )
    res = await db.execute(stmt)
    run = res.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    return {"run_id": run.id, "status": run.status, "planner_mode": run.planner_mode}


# ---------------------------------------------------------------------------
# Feature 3: Clarification routes
# ---------------------------------------------------------------------------

async def _load_graph_for_ranking(
    ctx: BATenantContext, db: AsyncSession
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Loads this project's nodes/edges in the shape the gap-ranking engine expects.

    Shared by the REST clarifications/next endpoint and the clarifications/stream
    WebSocket so both drive the same ranking logic off one query.
    """
    stmt_nodes = select(BaNode).where(
        BaNode.project_id == ctx.project_id,
        BaNode.org_id == ctx.org_id,
    )
    stmt_edges = select(BaEdge).where(
        BaEdge.project_id == ctx.project_id,
        BaEdge.org_id == ctx.org_id,
    )
    nodes_res = await db.execute(stmt_nodes)
    edges_res = await db.execute(stmt_edges)
    nodes = [
        {"id": n.id, "type": n.type, "attrs": n.attrs or {}}
        for n in nodes_res.scalars().all()
    ]
    edges = [
        {"source_id": e.source_node_id, "target_id": e.target_node_id}
        for e in edges_res.scalars().all()
    ]
    # Upload extraction writes immutable requirement and gap facts, while the
    # older planner writes BaNode. Project the current source-backed gaps into
    # the same ranking shape so Questions works immediately after an upload.
    facts = await get_facts(ctx, db)
    requirements = {
        fact.subject_key: fact for fact in facts
        if fact.subject_type == "Requirement" and fact.predicate == "specified_as"
    }
    gap_fields: Dict[str, set[str]] = {}
    for fact in facts:
        if fact.subject_type == "Gap" and fact.predicate in {"missing_information", "ambiguous_information"}:
            value = fact.value or {}
            key, field = value.get("requirement_key"), value.get("field")
            if key in requirements and field:
                gap_fields.setdefault(key, set()).add(field)
    existing_ids = {node["id"] for node in nodes}
    for key, fields in gap_fields.items():
        if key not in existing_ids:
            nodes.append({"id": key, "type": "Requirement", "attrs": {field: None for field in sorted(fields)}})
    # A project also needs the stable business analysis narrative, even when
    # uploaded requirements happen to contain no structural gaps.
    source_exists = (await db.execute(select(BaSource.id).where(
        BaSource.project_id == ctx.project_id,
        BaSource.org_id == ctx.org_id,
        BaSource.kind.in_(["chat", "document"]),
    ).limit(1))).scalar_one_or_none()
    if facts or source_exists:
        project = await db.get(BaProject, ctx.project_id)
        settings = project.settings or {}
        summary = settings.get("project_summary") or {}
        summary_fields = {
            "purpose": "project_purpose", "problem": "problem_statement",
            "stakeholders": "stakeholders", "success_measures": "success_measures",
            "must_have": "must_have_features", "should_have": "should_have_features",
            "could_have": "could_have_features", "wont_have": "wont_have_features",
            "constraints": "constraints", "risks_dependencies": "risks",
        }
        answered_keys = {
            fact.subject_key for fact in facts
            if fact.subject_type == "ClarificationQuestion" and fact.predicate == "answered"
        }
        attrs = {
            field: ("answered" if gap_subject_key("project-discovery", field) in answered_keys or summary.get(summary_fields[field]) or (field in {"must_have", "should_have"} and settings.get(field)) else None)
            for field in PROJECT_DISCOVERY_QUESTIONS
        }
        nodes.append({"id": "project-discovery", "type": "ProjectDiscovery", "attrs": attrs})
    return nodes, edges


@ba_router.get("/projects/{project_id}/clarifications/next")
async def get_next_clarification_endpoint(
    project_id: str,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Returns the next unresolved clarifying question for the project.

    Process: rank gaps from current graph, resume an unanswered question when
    one exists, otherwise choose among eligible gaps and record a new question.
    Returns {"question": null, "gap_key": null} when all gaps are resolved.
    """
    nodes, edges = await _load_graph_for_ranking(ctx, db)
    result = await get_next_clarification(ctx, db, nodes, edges)
    if result is not None and not result.get("resumed"):
        await db.commit()

    if result is None:
        return {"question": None, "gap_key": None, "message": "All open questions are answered."}
    return result


@ba_router.post("/projects/{project_id}/clarifications/{gap_key}/answer")
async def answer_clarification_endpoint(
    project_id: str,
    gap_key: str,
    req: ClarificationAnswerRequest,
    background_tasks: BackgroundTasks,
    ctx: BATenantContext = Depends(get_ba_tenant_deps),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Records the stakeholder's answer, runs conflict detection, and returns the next question.

    If the answer conflicts with a prior answer for the same gap, returns a
    conflict_notice explaining both values and asking which should stand.
    Unless the turn decision is move_on, `next_question` is prepared and recorded
    in the same request. The slow scope rewrite runs after the response is sent.
    """
    result = await record_clarification_answer(ctx, db, gap_key, req.answer)
    project = await db.get(BaProject, ctx.project_id)
    project.settings = {**(project.settings or {}), "scope_approved": False}
    await db.commit()
    next_question = None
    if req.next_step != "move_on":
        nodes, edges = await _load_graph_for_ranking(ctx, db)
        next_question = await get_next_clarification(ctx, db, nodes, edges)
        await db.commit()
    background_tasks.add_task(_regenerate_summary_after_response, ctx)
    return {
        **result,
        "next_question": None if req.next_step == "move_on" else next_question or {"question": None, "gap_key": None, "message": "All open questions are answered."},
    }


async def _regenerate_summary_after_response(ctx: BATenantContext) -> None:
    """Rewrites the project scope on its own session once the answer response has been sent."""
    try:
        async with get_async_session() as db:
            project = await db.get(BaProject, ctx.project_id)
            if project is not None:
                await regenerate_project_summary(ctx, db, project)
    except Exception:
        logger.exception("BA background scope rewrite failed project_id=%s", ctx.project_id)


# ---------------------------------------------------------------------------
# Clarification stream — WebSocket
# ---------------------------------------------------------------------------
# On its own router (no `dependencies=`): ba_router's router-level
# get_ba_tenant_deps expects a `request: Request` with an Authorization header,
# which a browser WebSocket handshake cannot set. Mounted separately in
# app/main.py; auth is done by hand below via get_ba_tenant_context_from_token,
# same JWT/exp/org_id/BaProject-lookup rules, token read from ?token= instead
# (matches the precedent in app/brain_routes.py's main_brain_stream).

ba_ws_router = APIRouter()


async def _stream_text_tokens(websocket: WebSocket, text: str, msg_type: str = "token", delay: float = 0.02) -> None:
    """Chunks an already-phrased string into word tokens over the socket.

    ponytail: phrase_clarification_question/phrase_conflict_notice call
    get_structured_output, which returns the finished JSON-schema string in one shot —
    there's no provider token stream to relay for a single schema field, and parsing
    partial JSON just to stream one sentence isn't worth it. This simulates the reveal
    instead. Upgrade path: swap to a raw streaming completion if questions grow past
    a sentence and the fake cadence stops feeling honest.
    """
    words = text.split(" ")
    for i, word in enumerate(words):
        suffix = " " if i < len(words) - 1 else ""
        await websocket.send_json({"type": msg_type, "text": word + suffix})
        await asyncio.sleep(delay)


@ba_ws_router.websocket("/api/ba/projects/{project_id}/clarifications/stream")
async def clarifications_stream(websocket: WebSocket, project_id: str) -> None:
    """Streams the clarification Q&A loop over a WebSocket.

    Protocol:
      server -> {"type": "status", "stage": "thinking"}
      server -> {"type": "token", "text": "..."} *              (question, word by word)
      server -> {"type": "question", "gap_key", "node_type", "field", "score"}
             or {"type": "done", "message": "All open questions are answered."}   (then closes)
      client -> {"answer": "..."}
      server -> {"type": "status", "stage": "recording"}
      server -> {"type": "conflict", "text": "..."} *           (only if a conflict was detected)
      server -> {"type": "conflict_done"}
      ... loops back to "thinking" for the next question automatically ...
    """
    await websocket.accept()
    token = websocket.query_params.get("token")
    if not token:
        await websocket.send_json({"type": "error", "detail": "Missing ?token= query parameter."})
        await websocket.close(code=1008)
        return

    try:
        async with get_async_session() as db:
            ctx = await get_ba_tenant_context_from_token(token, project_id, db)

            while True:
                await websocket.send_json({"type": "status", "stage": "thinking"})
                nodes, edges = await _load_graph_for_ranking(ctx, db)
                result = await get_next_clarification(ctx, db, nodes, edges)
                await db.commit()

                if result is None:
                    await websocket.send_json({"type": "done", "message": "All open questions are answered."})
                    break

                await _stream_text_tokens(websocket, result["question"])
                await websocket.send_json(
                    {
                        "type": "question",
                        "gap_key": result["gap_key"],
                        "node_type": result.get("node_type"),
                        "field": result.get("field"),
                        "score": result.get("score", 0.0),
                    }
                )

                incoming = await websocket.receive_json()
                answer = str((incoming or {}).get("answer", "")).strip()
                if not answer:
                    await websocket.send_json({"type": "error", "detail": 'Expected {"answer": "..."}'})
                    continue

                await websocket.send_json({"type": "status", "stage": "recording"})
                answer_result = await record_clarification_answer(ctx, db, result["gap_key"], answer)
                await db.commit()

                if answer_result.get("conflict_notice"):
                    await _stream_text_tokens(websocket, answer_result["conflict_notice"], msg_type="conflict")
                    await websocket.send_json({"type": "conflict_done"})
    except WebSocketDisconnect:
        return
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "detail": exc.detail})
        await websocket.close(code=1008)
    except JevDecisionError:
        await websocket.send_json({"type": "error", "detail": VALIDATION_UNAVAILABLE})
        await websocket.close(code=1011)
    except Exception:  # noqa: BLE001 — surface the failure to the client, not a silent drop
        logger.exception("BA websocket stream failed")
        await websocket.send_json({"type": "error", "detail": "Something went wrong on our side. Please try again."})
        await websocket.close(code=1011)


@ba_ws_router.websocket("/api/ba/projects/{project_id}/chat/stream")
async def chat_stream(websocket: WebSocket, project_id: str) -> None:
    """Streams real chat-driven fact extraction over a WebSocket, Claude-style word-by-word.

    Protocol:
      client -> {"message": "..."}
      server -> {"type": "status", "stage": "thinking"}
      server -> {"type": "token", "text": "..."} *   (acknowledgment, word by word)
      server -> {"type": "done", "facts_created", "goal_facts", "entity_facts", "ir"}
      ... loops, accepting another message on the same socket ...
    """
    await websocket.accept()
    token = websocket.query_params.get("token")
    if not token:
        await websocket.send_json({"type": "error", "detail": "Missing ?token= query parameter."})
        await websocket.close(code=1008)
        return

    try:
        async with get_async_session() as db:
            ctx = await get_ba_tenant_context_from_token(token, project_id, db)

            while True:
                incoming = await websocket.receive_json()
                message = str((incoming or {}).get("message", "")).strip()
                if not message:
                    await websocket.send_json({"type": "error", "detail": 'Expected {"message": "..."}'})
                    continue

                await websocket.send_json({"type": "status", "stage": "thinking"})
                source = await _get_or_create_chat_source(ctx, db)
                result = await _extract_and_regenerate_summary(
                    ctx, db, text=message, source_id=source.id, asserted_by="ba_chat_extraction",
                )

                await _stream_text_tokens(websocket, result["reply"])
                await websocket.send_json({
                    "type": "done",
                    "facts_created": result["facts_created"],
                    "goal_facts": result["goal_facts"],
                    "entity_facts": result["entity_facts"],
                    "ir": result["ir"].model_dump(),
                    "project_summary": result["project_summary"],
                    "summary_updated_at": result["summary_updated_at"],
                })
    except WebSocketDisconnect:
        return
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "detail": exc.detail})
        await websocket.close(code=1008)
    except JevDecisionError:
        await websocket.send_json({"type": "error", "detail": VALIDATION_UNAVAILABLE})
        await websocket.close(code=1011)
    except Exception:  # noqa: BLE001 — surface the failure to the client, not a silent drop
        logger.exception("BA websocket stream failed")
        await websocket.send_json({"type": "error", "detail": "Something went wrong on our side. Please try again."})
        await websocket.close(code=1011)
