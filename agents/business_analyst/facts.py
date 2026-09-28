"""Repo functions for the BA OS append-only fact store.

See docs/superpowers/specs/2026-08-03-ba-fact-store-design.md. Every function here takes
ctx: BATenantContext first and filters by ctx.org_id in the same query as everything else —
never filter tenant scope in Python after an unscoped read.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from agents.business_analyst.models import BaFact, BaProject, BaSource


@dataclass(frozen=True)
class BATenantContext:
    """Constructed by hand (or by tests) until a later phase wires it to a real JWT — see
    architecture_plan.md §6.2. The shape is fixed now so no repo function's signature needs to
    change when that phase lands."""

    org_id: str
    project_id: str


async def register_source(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    kind: str,
    tier: str,
    content_hash: str,
    ref: str | None = None,
    stakeholder_id: str | None = None,
) -> BaSource:
    project = await session.get(BaProject, ctx.project_id)
    if project is None or project.org_id != ctx.org_id:
        raise ValueError(f"project {ctx.project_id} not found in org {ctx.org_id}")

    source = BaSource(
        id=str(uuid.uuid4()),
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        kind=kind,
        tier=tier,
        ref=ref,
        stakeholder_id=stakeholder_id,
        captured_at=datetime.now(timezone.utc),
        content_hash=content_hash,
    )
    session.add(source)
    await session.flush()
    return source


async def assert_fact(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    subject_type: str,
    subject_key: str,
    predicate: str,
    source_id: str,
    asserted_by: str,
    value: dict | None = None,
    object_type: str | None = None,
    object_key: str | None = None,
    run_id: str | None = None,
    replaces: str | None = None,
) -> BaFact:
    source = await session.get(BaSource, source_id)
    if source is None or source.org_id != ctx.org_id or source.project_id != ctx.project_id:
        raise ValueError(f"source {source_id} not found in project {ctx.project_id}")

    # No human_approval parameter, deliberately — see module docstring and Task 7's approve_fact.
    fact = BaFact(
        id=str(uuid.uuid4()),
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        subject_type=subject_type,
        subject_key=subject_key,
        predicate=predicate,
        value=value,
        object_type=object_type,
        object_key=object_key,
        source_id=source_id,
        run_id=run_id,
        human_approval=False,
        asserted_at=datetime.now(timezone.utc),
        asserted_by=asserted_by,
        replaces=replaces,
    )
    session.add(fact)
    await session.flush()
    return fact


async def get_facts(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    subject_key: str | None = None,
    predicate: str | None = None,
) -> list[BaFact]:
    """Returns current facts only: a fact is excluded iff some other fact's `replaces` points
    at it, which correctly resolves chains of any length without a recursive CTE — a fact with
    nothing pointing at it is, by construction, the most recent version."""
    Replacement = aliased(BaFact)
    superseded = (
        select(Replacement.id)
        .where(
            Replacement.replaces == BaFact.id,
            Replacement.org_id == ctx.org_id,
            Replacement.project_id == ctx.project_id,
        )
        .exists()
    )

    stmt = (
        select(BaFact)
        .where(BaFact.org_id == ctx.org_id, BaFact.project_id == ctx.project_id)
        .where(~superseded)
        .order_by(BaFact.seq)
    )
    if subject_key is not None:
        stmt = stmt.where(BaFact.subject_key == subject_key)
    if predicate is not None:
        stmt = stmt.where(BaFact.predicate == predicate)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def approve_fact(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    fact_id: str,
    asserted_by: str,
) -> BaFact:
    """The only function that can produce a fact with human_approval=True. ba_fact is
    immutable (a DB trigger blocks UPDATE unconditionally), so approval cannot flip a column
    on the existing row — instead this reads the target fact and inserts a new one, identical
    except human_approval=True and replaces=fact_id."""
    # Local import: api.security imports this module at top level.
    from agents.business_analyst.api.security import set_approval_context

    original = await session.get(BaFact, fact_id)
    if original is None or original.org_id != ctx.org_id or original.project_id != ctx.project_id:
        raise ValueError(f"fact {fact_id} not found in project {ctx.project_id}")

    # Approving a superseded fact (or approving twice) would give the chain a second head.
    # ponytail: check-then-insert races under concurrent approvals; the real fix is restoring
    # the partial unique index ba_0003_unique_replaces on ba_fact.replaces (missing from models/fact.py).
    already_replaced = await session.scalar(
        select(BaFact.id)
        .where(
            BaFact.replaces == fact_id,
            BaFact.org_id == ctx.org_id,
            BaFact.project_id == ctx.project_id,
        )
        .limit(1)
    )
    if already_replaced is not None:
        raise ValueError(f"fact {fact_id} is already superseded by {already_replaced}")

    approved = BaFact(
        id=str(uuid.uuid4()),
        project_id=original.project_id,
        org_id=original.org_id,
        subject_type=original.subject_type,
        subject_key=original.subject_key,
        predicate=original.predicate,
        value=original.value,
        object_type=original.object_type,
        object_key=original.object_key,
        source_id=original.source_id,
        run_id=original.run_id,
        human_approval=True,
        asserted_at=datetime.now(timezone.utc),
        asserted_by=asserted_by,
        replaces=original.id,
    )
    set_approval_context(True)
    try:
        session.add(approved)
        await session.flush()
    finally:
        set_approval_context(False)
    return approved
