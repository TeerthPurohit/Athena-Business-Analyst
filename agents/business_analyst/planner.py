"""Deterministic Planner for BA OS (§7.1, §9).

Backward-chains target goal outputs to capabilities in the catalog to build a topological execution DAG.
CLAUDE.md Non-Negotiables: Pure function of (graph_state, capability_catalog, target_goal) -> execution_plan. ZERO LLM calls.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from agents.business_analyst.capability import CapabilitySpec
from agents.business_analyst.constraint_engine import evaluate_conditions
from agents.business_analyst.orchestration.dag import WaveNode, group_into_waves

MAX_ATTEMPTS = 2  # Thrash guard per research_engine.py:477's `attempts < 2`


class PlanStatus(str, Enum):
    READY = "ready"
    UNSATISFIABLE = "unsatisfiable"
    CYCLE_DETECTED = "cycle_detected"
    INVALID_CONDITION = "invalid_condition"


class _InvalidConditionError(Exception):
    """A capability's condition was rejected by the Constraint Engine (bad operator/path/shape)."""


@dataclass
class ExecutionStep:
    step_id: int
    capability_key: str
    capability: CapabilitySpec
    matched_inputs: Dict[str, Any] = field(default_factory=dict)
    produced_outputs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionWave:
    wave_id: int
    steps: List[ExecutionStep] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    status: PlanStatus
    waves: List[ExecutionWave] = field(default_factory=list)
    missing_inputs: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


def build_execution_plan(
    target_outputs: List[str],
    available_capabilities: List[CapabilitySpec],
    graph_state: Dict[str, Any],
    attempt_history: Optional[Dict[str, int]] = None,
) -> ExecutionPlan:
    """Builds a deterministic wave-structured execution plan backward-chaining target outputs.

    Rules enforced:
    - Single-producer-per-type solver rule
    - Thrash guard: excludes capabilities with attempt_history >= 2
    - Conditions evaluated once against pre-plan state
    - Zero LLM calls
    """
    if not target_outputs:
        return ExecutionPlan(status=PlanStatus.READY, waves=[])

    attempt_history = attempt_history or {}

    # Filter out capabilities that reached thrash guard cap (attempts >= 2)
    valid_caps = [
        cap for cap in available_capabilities
        if attempt_history.get(cap.key, 0) < MAX_ATTEMPTS
    ]

    # Index capabilities by output types/keys
    cap_map: Dict[str, List[CapabilitySpec]] = {}
    for cap in valid_caps:
        if not cap.outputs:
            continue
        out_types = cap.outputs.get("types", [])
        if isinstance(out_types, str):
            out_types = [out_types]
        if "key" in cap.outputs and isinstance(cap.outputs["key"], str):
            out_types.append(cap.outputs["key"])

        for out in out_types:
            cap_map.setdefault(out, []).append(cap)

    # Track available graph types/keys in graph_state
    available_state_keys = set(graph_state.get("available_types", []))
    if "nodes" in graph_state and isinstance(graph_state["nodes"], dict):
        available_state_keys.update(graph_state["nodes"].keys())

    selected_caps: Dict[str, CapabilitySpec] = {}  # key -> CapabilitySpec
    producers_by_type: Dict[str, str] = {}  # type -> capability_key (Single-producer-per-type)
    missing: List[str] = []

    visiting: Set[str] = set()
    visited: Set[str] = set()

    def resolve_goal(goal: str) -> bool:
        if goal in available_state_keys:
            return True

        # Single-producer-per-type: if a producer is already selected for this type, return True
        if goal in producers_by_type:
            return True

        candidates = cap_map.get(goal, [])
        if not candidates:
            missing.append(goal)
            return False

        # Pick single candidate whose preconditions pass against pre-plan graph_state
        selected_candidate = None
        for cand in candidates:
            if cand.key in visiting:
                raise ValueError(f"Dependency cycle detected involving capability '{cand.key}'")

            try:
                passes = evaluate_conditions(cand.conditions, graph_state)
            except ValueError as cond_err:
                raise _InvalidConditionError(f"Capability '{cand.key}': {cond_err}") from cond_err
            if passes:
                selected_candidate = cand
                break

        if not selected_candidate:
            missing.append(goal)
            return False

        if selected_candidate.key in visited:
            producers_by_type[goal] = selected_candidate.key
            return True

        visiting.add(selected_candidate.key)

        # Backward chain inputs required by candidate
        in_types = []
        if selected_candidate.inputs:
            in_types = selected_candidate.inputs.get("types", [])
            if isinstance(in_types, str):
                in_types = [in_types]

        for req_in in in_types:
            if not resolve_goal(req_in):
                visiting.remove(selected_candidate.key)
                return False

        visiting.remove(selected_candidate.key)
        visited.add(selected_candidate.key)
        selected_caps[selected_candidate.key] = selected_candidate
        producers_by_type[goal] = selected_candidate.key

        return True

    try:
        all_resolved = True
        for target in target_outputs:
            if not resolve_goal(target):
                all_resolved = False

        if not all_resolved:
            return ExecutionPlan(
                status=PlanStatus.UNSATISFIABLE,
                missing_inputs=sorted(list(set(missing))),
                error_message=f"Missing required inputs/capabilities for: {sorted(list(set(missing)))}",
            )

        # Build WaveNodes for DAG wave grouping via research_engine.py waves()
        wave_nodes: List[WaveNode] = []
        for cap_key, cap in selected_caps.items():
            deps = []
            if cap.inputs:
                in_types = cap.inputs.get("types", [])
                if isinstance(in_types, str):
                    in_types = [in_types]
                for req_in in in_types:
                    producer = producers_by_type.get(req_in)
                    if producer and producer != cap_key and producer not in deps:
                        deps.append(producer)

            step = ExecutionStep(
                step_id=0,
                capability_key=cap_key,
                capability=cap,
                matched_inputs=cap.inputs or {},
                produced_outputs=cap.outputs or {},
            )
            wave_nodes.append(WaveNode(id=cap_key, depends_on=deps, payload=step))

        # Group nodes into parallel execution waves using Stack B's waves() algorithm
        grouped_waves = group_into_waves(wave_nodes)

        step_counter = 1
        execution_waves: List[ExecutionWave] = []
        for wave_idx, node_wave in enumerate(grouped_waves):
            wave_steps: List[ExecutionStep] = []
            for node in node_wave:
                step: ExecutionStep = node.payload
                step.step_id = step_counter
                step_counter += 1
                wave_steps.append(step)
            execution_waves.append(ExecutionWave(wave_id=wave_idx + 1, steps=wave_steps))

        return ExecutionPlan(status=PlanStatus.READY, waves=execution_waves)

    except _InvalidConditionError as cond_err:
        return ExecutionPlan(
            status=PlanStatus.INVALID_CONDITION,
            error_message=str(cond_err),
        )
    except ValueError as cycle_err:
        return ExecutionPlan(
            status=PlanStatus.CYCLE_DETECTED,
            error_message=str(cycle_err),
        )


def replan_next_wave(
    target_outputs: List[str],
    available_capabilities: List[CapabilitySpec],
    current_graph_state: Dict[str, Any],
    attempt_history: Dict[str, int],
) -> ExecutionPlan:
    """Replans after a wave execution, using updated graph state and attempt history."""
    return build_execution_plan(
        target_outputs=target_outputs,
        available_capabilities=available_capabilities,
        graph_state=current_graph_state,
        attempt_history=attempt_history,
    )
