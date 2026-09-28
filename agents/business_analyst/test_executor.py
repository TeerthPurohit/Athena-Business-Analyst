"""Tests for Executor Engine & Celery Tasks (§7.1, §7.2, §7.4, §9).

Mandatory Verification Tests:
1. Fencing-conditional lease acquisition: single atomic UPDATE prevents duplicate dispatch.
2. Stalled lease reclamation: kill worker mid-run -> expired lease reclaimed by reap_stalled_runs.
3. Atomic single-transaction fact commit: failed or crashed worker leaves ZERO partial facts in DB.
4. Wave linkage: executor consumes Phase 4 ExecutionPlan waves directly.
"""
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select

from agents.business_analyst.capability import CapabilitySpec
from agents.business_analyst.executor import BaExecutor
from agents.business_analyst.models import BaFact, BaProject, BaRun, BaRunTask, BaSource
from agents.business_analyst.planner import ExecutionPlan, ExecutionStep, ExecutionWave, PlanStatus
from agents.business_analyst.tasks.ba_tasks import (
    acquire_task_lease,
    execute_ba_capability_sync,
    reap_stalled_runs_sync,
)


@pytest.mark.asyncio
async def test_fencing_conditional_lease_acquisition(db_session, ba_project) -> None:
    """Asserts that lease acquisition via single conditional UPDATE prevents duplicate worker dispatch."""
    def _test(session):
        run = BaRun(
            project_id=ba_project.id,
            org_id=ba_project.org_id,
            status="pending",
        )
        session.add(run)
        session.flush()

        task = BaRunTask(
            run_id=run.id,
            capability_id="derive_requirements",
            org_id=ba_project.org_id,
            status="pending",
            idempotency_key=f"{run.id}:derive_requirements",
        )
        session.add(task)
        session.flush()

        # Worker 1 attempts to acquire lease
        acq1 = acquire_task_lease(
            session=session,
            run_id=run.id,
            capability_id="derive_requirements",
            worker_id="worker-1",
            lease_ttl_seconds=300,
        )
        assert acq1 is True

        # Worker 2 attempts to acquire lease on same task while live lease exists
        acq2 = acquire_task_lease(
            session=session,
            run_id=run.id,
            capability_id="derive_requirements",
            worker_id="worker-2",
            lease_ttl_seconds=300,
        )
        assert acq2 is False

    await db_session.run_sync(_test)


@pytest.mark.asyncio
async def test_stalled_lease_reclamation(db_session, ba_project) -> None:
    """Asserts that an expired lease from a killed worker is reclaimed by reap_stalled_runs."""
    def _test(session):
        run = BaRun(
            project_id=ba_project.id,
            org_id=ba_project.org_id,
            status="running",
        )
        session.add(run)
        session.flush()

        # Create a task with an expired lease (simulate dead worker)
        expired_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        task = BaRunTask(
            run_id=run.id,
            capability_id="derive_requirements",
            org_id=ba_project.org_id,
            status="running",
            worker_id="dead-worker",
            lease_expires_at=expired_time,
            idempotency_key=f"{run.id}:derive_requirements",
        )
        session.add(task)
        session.flush()

        # Run reaper
        reclaimed_count = reap_stalled_runs_sync(session)
        assert reclaimed_count == 1

        # Task status should now be 'reclaimable'
        updated_task = session.execute(
            select(BaRunTask).where(BaRunTask.id == task.id)
        ).scalar_one()
        assert updated_task.status == "reclaimable"

        # Now a new worker can acquire the lease
        acq_new = acquire_task_lease(
            session=session,
            run_id=run.id,
            capability_id="derive_requirements",
            worker_id="worker-new",
        )
        assert acq_new is True

    await db_session.run_sync(_test)


@pytest.mark.asyncio
async def test_atomic_single_transaction_fact_commit_on_failure(db_session, ba_project) -> None:
    """Asserts that a failed execution rolls back and leaves ZERO partial facts in Postgres."""
    def _test(session):
        source = BaSource(
            project_id=ba_project.id,
            org_id=ba_project.org_id,
            kind="document",
            tier="tier1",
            captured_at=datetime.now(timezone.utc),
            content_hash="sha_test_123",
        )
        session.add(source)
        session.flush()

        run = BaRun(
            project_id=ba_project.id,
            org_id=ba_project.org_id,
            status="running",
        )
        session.add(run)
        session.flush()

        task = BaRunTask(
            run_id=run.id,
            capability_id="faulty_capability",
            org_id=ba_project.org_id,
            status="pending",
            idempotency_key=f"{run.id}:faulty_capability",
        )
        session.add(task)
        session.flush()

        # Malformed fact data (missing required subject_type)
        bad_fact_data = [
            {"subject_key": "k1", "predicate": "p1"}
        ]

        with pytest.raises(Exception):
            execute_ba_capability_sync(
                session=session,
                run_id=run.id,
                capability_id="faulty_capability",
                org_id=ba_project.org_id,
                project_id=ba_project.id,
                source_id=source.id,
                produced_facts_data=bad_fact_data,
            )

        # Assert zero facts were committed
        facts = session.execute(
            select(BaFact).where(BaFact.run_id == run.id)
        ).scalars().all()
        assert len(facts) == 0

    await db_session.run_sync(_test)


@pytest.mark.asyncio
async def test_executor_consumes_phase4_plan_waves_directly(db_session, ba_project) -> None:
    """Asserts that BaExecutor consumes Phase 4 ExecutionPlan waves directly."""
    def _test(session):
        source = BaSource(
            project_id=ba_project.id,
            org_id=ba_project.org_id,
            kind="document",
            tier="tier1",
            captured_at=datetime.now(timezone.utc),
            content_hash="sha_wave_123",
        )
        session.add(source)
        session.flush()

        executor = BaExecutor(session=session)
        run = executor.create_run(
            project_id=ba_project.id,
            org_id=ba_project.org_id,
            target_outputs=["requirement"],
        )

        cap_spec = CapabilitySpec(
            key="derive_requirements",
            name="Derive Requirements",
            kind="derivation",
            outputs={"types": ["requirement"]},
        )

        step = ExecutionStep(
            step_id=1,
            capability_key="derive_requirements",
            capability=cap_spec,
        )
        wave = ExecutionWave(wave_id=1, steps=[step])
        plan = ExecutionPlan(status=PlanStatus.READY, waves=[wave])

        produced_facts = {
            "derive_requirements": [
                {
                    "subject_type": "Requirement",
                    "subject_key": "req_101",
                    "predicate": "to_be_spec",
                    "value": "Hospital stock monitoring requirement",
                }
            ]
        }

        res = executor.execute_plan(
            run=run,
            plan=plan,
            source_id=source.id,
            produced_facts_by_step=produced_facts,
        )

        assert res["status"] == "completed"
        assert res["executed_waves"] == 1

        # Verify facts were committed
        facts = session.execute(
            select(BaFact).where(BaFact.run_id == run.id)
        ).scalars().all()
        assert len(facts) == 1
        assert facts[0].subject_key == "req_101"

    await db_session.run_sync(_test)
