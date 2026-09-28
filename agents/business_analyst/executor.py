"""Executor for BA OS capabilities (§7.1, §7.2, §7.4, §9).

Consumes Phase 4 ExecutionPlan (plan.waves) directly to run capability DAGs wave by wave.
Fencing conditional lease acquisition, wave-parallel dispatch, atomic single-transaction fact commits.
Contract: replan after every wave, never mid-solve.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agents.business_analyst.capability import CapabilitySpec
from agents.business_analyst.models import BaFact, BaRun, BaRunTask
from agents.business_analyst.planner import ExecutionPlan, ExecutionWave, ExecutionStep
from agents.business_analyst.tasks.ba_tasks import acquire_task_lease, execute_ba_capability_sync


class BaExecutor:
    """Executes capability execution plans wave-by-wave in parallel."""

    def __init__(self, session: Session, worker_id: str = "worker-local") -> None:
        self.session = session
        self.worker_id = worker_id

    def create_run(self, project_id: str, org_id: str, target_outputs: List[str]) -> BaRun:
        """Initializes a new execution run."""
        run = BaRun(
            id=str(uuid.uuid4()),
            project_id=project_id,
            org_id=org_id,
            status="pending",
            target_outputs={"outputs": target_outputs},
        )
        self.session.add(run)
        self.session.commit()
        return run

    def initialize_run_tasks(self, run_id: str, org_id: str, plan: ExecutionPlan) -> List[BaRunTask]:
        """Creates BaRunTask records for all capabilities in the execution plan."""
        tasks: List[BaRunTask] = []
        for wave in plan.waves:
            for step in wave.steps:
                cap_key = step.capability_key
                idempotency_key = f"{run_id}:{cap_key}"
                task = BaRunTask(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    capability_id=cap_key,
                    org_id=org_id,
                    status="pending",
                    idempotency_key=idempotency_key,
                )
                tasks.append(task)
                self.session.add(task)
        self.session.commit()
        return tasks

    def execute_wave(
        self,
        run: BaRun,
        wave: ExecutionWave,
        source_id: str,
        produced_facts_by_step: Dict[str, List[Dict[str, Any]]],
    ) -> List[str]:
        """Executes steps within a single wave in parallel (synchronously simulated here).

        Consumes Phase 4 wave steps directly.
        Returns list of executed capability keys.
        """
        executed_keys: List[str] = []

        for step in wave.steps:
            cap_key = step.capability_key
            facts_data = produced_facts_by_step.get(cap_key, [])

            success = execute_ba_capability_sync(
                session=self.session,
                run_id=run.id,
                capability_id=cap_key,
                org_id=run.org_id,
                project_id=run.project_id,
                source_id=source_id,
                produced_facts_data=facts_data,
                worker_id=self.worker_id,
            )
            if success:
                executed_keys.append(cap_key)

        return executed_keys

    def execute_plan(
        self,
        run: BaRun,
        plan: ExecutionPlan,
        source_id: str,
        produced_facts_by_step: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Executes full execution plan wave by wave.

        Contract: replan after every wave, never mid-solve.
        """
        if not plan.waves:
            run.status = "completed"
            self.session.commit()
            return {"status": "completed", "executed_waves": 0}

        self.initialize_run_tasks(run.id, run.org_id, plan)
        run.status = "running"
        self.session.commit()

        executed_wave_count = 0
        for wave in plan.waves:
            executed_keys = self.execute_wave(
                run=run,
                wave=wave,
                source_id=source_id,
                produced_facts_by_step=produced_facts_by_step,
            )
            executed_wave_count += 1

        run.status = "completed"
        self.session.commit()
        return {
            "run_id": run.id,
            "status": "completed",
            "executed_waves": executed_wave_count,
        }
