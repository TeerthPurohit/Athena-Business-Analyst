"""Security layer for BA OS (§0, §6, CLAUDE.md non-negotiables).

CLAUDE.md Non-Negotiables:
- get_ba_tenant_context(): JWT-only auth, NO header fallback, NO query parameter fallback, NO default company.
- session parameter is REQUIRED — database validation against BaProject CANNOT be bypassed.
- Reject sub/workspaceId as tenant key.
- exp claim required.
- Foreign/missing project -> 404 Not Found (verified strictly via mandatory BaProject database table lookup).
- ORM before_flush guard protecting human_approval on BaFact AND status='approved' on BaDeliverableInstance.
- Unicode neutralization (zero-width, bidi overrides, tag characters).
- Redis channel namespacing channel:ba:{org_id}:{session_id}.
- No print(), no fact bodies in logs — step labels, counts, and ids only.
"""
import contextvars
import logging
import os
import re
from typing import Any, Dict, Optional, Union
import jwt
from fastapi import HTTPException, Request, status
from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.models import BaProject

logger = logging.getLogger("BA_Security")

# Regex for dangerous/invisible Unicode characters: zero-width, bidi overrides, tag characters
UNICODE_SANATIZE_REGEX = re.compile(r"[\u200B-\u200D\uFEFF\u202A-\u202E\u2066-\u2069\U000E0000-\U000E007F]")


def sanitize_unicode_content(text: str) -> str:
    """Neutralizes zero-width, bidi override, and tag characters from ingested text."""
    if not text:
        return ""
    return UNICODE_SANATIZE_REGEX.sub("", text)


def get_ba_redis_channel(org_id: str, session_id: str) -> str:
    """Formats namespaced Redis channel name for BA OS events."""
    if not org_id or not session_id:
        raise ValueError("Both org_id and session_id are required for Redis channel namespacing.")
    return f"channel:ba:{org_id}:{session_id}"


def log_ba_step(logger_instance: logging.Logger, step_label: str, count: int, item_id: str) -> None:
    """Logs BA OS execution metadata only. NEVER logs fact bodies or text values."""
    logger_instance.info("BA_STEP label=%s count=%d id=%s", step_label, count, item_id)


async def _decode_org_claim(request: Request, secret_key: Optional[str] = None) -> str:
    """Validates Bearer JWT and extracts org_id claim. Raises 401 on all failure paths.

    Shared by get_ba_tenant_context (project routes) and get_ba_org_context (project-less routes).
    Rules: JWT-only, no header fallback, no query-param fallback, exp required, sub/workspaceId rejected.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. JWT token required.",
        )

    return await _decode_org_claim_from_token(auth_header.split(" ")[1].strip(), secret_key)


async def _decode_org_claim_from_token(token: str, secret_key: Optional[str] = None) -> str:
    """Validates a raw JWT string and extracts org_id claim. Raises 401 on all failure paths.

    Split out of _decode_org_claim so WebSocket routes — which cannot set a custom
    Authorization header from browser JS — can run the identical validation against a
    token sourced from the connection's query string instead of a header, without
    duplicating the exp/sub/workspaceId rules.
    """
    key_to_use = secret_key or os.getenv("JWT_ACCESS_SECRET")
    if not key_to_use:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT secret key configuration missing.",
        )

    try:
        payload = jwt.decode(
            token,
            key_to_use,
            algorithms=["HS256"],
            options={"verify_exp": True, "require": ["exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid JWT token: {str(exc)}",
        )

    # Reject sub or workspaceId as tenant key. orgId (camelCase) is accepted alongside
    # org_id/organization_id remain accepted for compatibility with older tokens.
    # The local Python account service issues org_id.
    org_id = payload.get("org_id") or payload.get("organization_id") or payload.get("orgId")
    if not org_id:
        if payload.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Claim 'sub' cannot be used as tenant org_id key.",
            )
        if payload.get("workspaceId"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Claim 'workspaceId' cannot be used as tenant org_id key.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT token missing required 'org_id' tenant claim.",
        )

    # Check for conflicting org claims in payload — all three accepted claim names, not just two
    if len({str(payload[k]) for k in ("org_id", "organization_id", "orgId") if payload.get(k)}) > 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Conflicting organization claims in JWT token.",
        )

    return str(org_id)


async def get_ba_org_context(
    request: Request,
    secret_key: Optional[str] = None,
) -> str:
    """JWT-only org_id extraction — no BaProject DB lookup.

    For routes that do not yet have a project_id (project create/list).
    Returns the validated org_id string. All 401 rules from _decode_org_claim apply.
    """
    return await _decode_org_claim(request, secret_key)


async def get_ba_tenant_context(
    request: Request,
    session: Union[AsyncSession, Session],
    secret_key: Optional[str] = None,
) -> BATenantContext:
    """Extracts and validates BATenantContext strictly from Bearer JWT and BaProject DB table.

    Strict security rules:
    - session parameter is REQUIRED (no default value) — DB validation CANNOT be bypassed.
    - NO header fallback (e.g. X-Organization-Id is IGNORED).
    - NO query parameter fallback.
    - NO DEFAULT_COMPANY_ID or hardcoded fallback secrets.
    - Token missing or invalid -> 401 Unauthorized.
    - Token missing exp claim -> 401 Unauthorized.
    - sub and workspaceId rejected as tenant key.
    - Foreign or missing project -> 404 Not Found (mandatory BaProject DB lookup).
    """
    org_id = await _decode_org_claim(request, secret_key)
    project_id = request.path_params.get("project_id") or request.query_params.get("project_id")
    return await _resolve_tenant_context(org_id, project_id, session)


async def get_ba_tenant_context_from_token(
    token: str,
    project_id: str,
    session: Union[AsyncSession, Session],
    secret_key: Optional[str] = None,
) -> BATenantContext:
    """WebSocket counterpart of get_ba_tenant_context — same validation, token sourced
    from the connection's query string instead of a header (see _decode_org_claim_from_token).
    """
    org_id = await _decode_org_claim_from_token(token, secret_key)
    return await _resolve_tenant_context(org_id, project_id, session)


async def _resolve_tenant_context(
    org_id: str,
    project_id: Optional[str],
    session: Union[AsyncSession, Session],
) -> BATenantContext:
    """Mandatory BaProject DB lookup shared by header- and token-based tenant resolution."""
    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project ID not specified.",
        )

    if isinstance(session, AsyncSession):
        project = await session.get(BaProject, str(project_id))
    else:
        project = session.get(BaProject, str(project_id))

    if project is None or project.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found for organization.",
        )

    return BATenantContext(org_id=org_id, project_id=str(project_id))


# Flag tracking authorized in-process approval context. A ContextVar, not a module global: a
# global stays set across `await session.flush()` and leaks to every concurrent coroutine.
# SQLAlchemy's greenlet_spawn copies the caller's context, so the flag is visible inside flush.
_APPROVAL_CONTEXT_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ba_approval_context_active", default=False
)


def set_approval_context(active: bool = True) -> None:
    _APPROVAL_CONTEXT_ACTIVE.set(active)


# class name -> (privileged attribute, privileged value, error message)
_PRIVILEGED_WRITES = {
    "BaFact": (
        "human_approval",
        True,
        "Privileged field 'human_approval' cannot be mutated directly. Use POST /facts/{id}/approve endpoint.",
    ),
    "BaDeliverableInstance": (
        "status",
        "approved",
        "Privileged field 'status' cannot be mutated directly to 'approved'. Use POST /deliverables/{id}/approve endpoint.",
    ),
}


def before_flush_privileged_guard(session: Session, flush_context: Any, instances: Any) -> None:
    """ORM before_flush listener protecting privileged fields (human_approval, status='approved').

    Blocks un-authorized modification or direct PATCH body field self-approval. Checks
    session.new as well as session.dirty — inserting a row that is already approved is the
    same self-approval as flipping an existing one. Bulk `update()` statements do not flush
    and so bypass this guard; none exist for these tables.
    """
    if _APPROVAL_CONTEXT_ACTIVE.get():
        return
    for obj in (*session.new, *session.dirty):
        rule = _PRIVILEGED_WRITES.get(obj.__class__.__name__)
        if rule is None:
            continue
        key, privileged_value, message = rule
        added = inspect(obj).attrs[key].history.added
        if added and added[0] == privileged_value:
            raise PermissionError(message)


# Registered on the Session class, which AsyncSession's sync_session is an instance of, so it
# covers every session in any process that imports this module (api/routes.py, deliverables.py,
# facts.approve_fact all do).
event.listen(Session, "before_flush", before_flush_privileged_guard)
