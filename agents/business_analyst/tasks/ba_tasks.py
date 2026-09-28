"""Celery tasks for the BA execution engine (§7.1, §7.2, §7.4, §9).

CLAUDE.md Non-Negotiables:
- Fencing-conditional lease acquisition via single conditional UPDATE.
- Facts buffered in worker memory, written in ONE atomic commit with task completion status.
- Zero partial facts on worker SIGKILL.
- Stalled task reaper (reap_stalled_runs) resets expired leases.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select, update, or_
from sqlalchemy.orm import Session

from agents.business_analyst.models import BaFact, BaRun, BaRunTask


def acquire_task_lease(
    session: Session,
    run_id: str,
    capability_id: str,
    worker_id: str = "worker-1",
    lease_ttl_seconds: int = 300,
) -> bool:
    """Acquires worker lease for a run task via a single fencing-conditional UPDATE.

    Returns True if lease acquired, False if task is already running or completed.
    """
    now = datetime.now(timezone.utc)
    lease_exp = now + timedelta(seconds=lease_ttl_seconds)

    stmt = (
        update(BaRunTask)
        .where(
            BaRunTask.run_id == run_id,
            BaRunTask.capability_id == capability_id,
            or_(
                BaRunTask.status == "pending",
                BaRunTask.status == "reclaimable",
                BaRunTask.lease_expires_at < now,
            ),
        )
        .values(
            status="running",
            worker_id=worker_id,
            lease_expires_at=lease_exp,
            updated_at=now,
        )
    )

    res = session.execute(stmt)
    session.flush()
    return res.rowcount > 0


def execute_ba_capability_sync(
    session: Session,
    run_id: str,
    capability_id: str,
    org_id: str,
    project_id: str,
    source_id: str,
    produced_facts_data: List[Dict[str, Any]],
    worker_id: str = "worker-1",
    lease_ttl_seconds: int = 300,
) -> bool:
    """Executes capability synchronously: buffers facts in worker memory and commits facts + completion in ONE transaction."""
    # 1. Acquire lease using fencing conditional UPDATE
    acquired = acquire_task_lease(
        session=session,
        run_id=run_id,
        capability_id=capability_id,
        worker_id=worker_id,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    if not acquired:
        return False  # Already running or completed

    try:
        # 2. Buffer produced facts in worker memory
        buffered_facts: List[BaFact] = []
        now = datetime.now(timezone.utc)

        for item in produced_facts_data:
            fact = BaFact(
                id=item.get("id") or str(uuid.uuid4()),
                project_id=project_id,
                org_id=org_id,
                subject_type=item["subject_type"],
                subject_key=item["subject_key"],
                predicate=item["predicate"],
                value=item.get("value"),
                object_type=item.get("object_type"),
                object_key=item.get("object_key"),
                source_id=source_id,
                run_id=run_id,
                asserted_at=now,
                asserted_by=f"capability:{capability_id}",
                replaces=item.get("replaces"),
            )
            buffered_facts.append(fact)

        # 3. Single atomic database transaction: write facts AND mark task completed
        session.add_all(buffered_facts)

        stmt_complete = (
            update(BaRunTask)
            .where(
                BaRunTask.run_id == run_id,
                BaRunTask.capability_id == capability_id,
                BaRunTask.status == "running",
            )
            .values(
                status="completed",
                updated_at=now,
            )
        )
        session.execute(stmt_complete)
        session.commit()
        return True

    except Exception as exc:
        session.rollback()
        # Mark task failed
        stmt_fail = (
            update(BaRunTask)
            .where(
                BaRunTask.run_id == run_id,
                BaRunTask.capability_id == capability_id,
            )
            .values(
                status="failed",
                error=str(exc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.execute(stmt_fail)
        session.commit()
        raise exc


def reap_stalled_runs_sync(session: Session) -> int:
    """Reclaims stalled runs whose lease has expired (lease_expires_at < now())."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(BaRunTask)
        .where(
            BaRunTask.status == "running",
            BaRunTask.lease_expires_at < now,
        )
        .values(
            status="reclaimable",
            worker_id=None,
            updated_at=now,
        )
    )
    res = session.execute(stmt)
    session.commit()
    return res.rowcount
