from contextlib import ExitStack
from unittest.mock import patch

import httpx
import pytest
from langchain_openai import ChatOpenAI

from agents.business_analyst.capability import CapabilityKind, CapabilitySpec
from agents.business_analyst.planner import ExecutionPlan, PlanStatus, build_execution_plan, replan_next_wave


def test_planner_100_runs_determinism_and_zero_llm():
    """Spec Checklist Test: Fixed state, 100 runs -> identical DAG; zero LLM calls."""
    cap_derive_req = CapabilitySpec(
        key="derive_requirements",
        kind=CapabilityKind.DERIVATION,
        name="Derive Requirements",
        inputs={"types": ["Goal"]},
        outputs={"types": ["Requirement"]},
    )
    cap_project_arch = CapabilitySpec(
        key="project_architecture",
        kind=CapabilityKind.PROJECTION,
        name="Project Architecture",
        inputs={"types": ["Requirement"]},
        outputs={"types": ["SystemArchitecture"]},
    )

    graph_state = {"available_types": ["Goal"]}

    # Every real BA LLM path: llm_client.get_structured_output (capabilities/semantic planner),
    # ChatOpenAI (project_harness), and httpx transport (jev_client + the OpenAI SDK underneath
    # ChatOpenAI). Patched at the class/transport level so an import-by-name inside planner.py
    # can't dodge the mock.
    llm_mocks = [
        patch("agents.business_analyst.llm_client.get_structured_output"),
        patch.object(ChatOpenAI, "invoke"),
        patch.object(ChatOpenAI, "ainvoke"),
        patch.object(httpx.Client, "send"),
        patch.object(httpx.AsyncClient, "send"),
    ]
    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in llm_mocks]
        # Initial run
        first_plan = build_execution_plan(
            target_outputs=["SystemArchitecture"],
            available_capabilities=[cap_derive_req, cap_project_arch],
            graph_state=graph_state,
        )

        assert first_plan.status == PlanStatus.READY
        assert len(first_plan.waves) == 2  # Wave 1: derive_requirements, Wave 2: project_architecture

        # Run 99 more times on fixed state -> MUST be 100% identical DAGs
        for _ in range(99):
            subsequent_plan = build_execution_plan(
                target_outputs=["SystemArchitecture"],
                available_capabilities=[cap_derive_req, cap_project_arch],
                graph_state=graph_state,
            )
            assert subsequent_plan.status == first_plan.status
            assert len(subsequent_plan.waves) == len(first_plan.waves)
            for w1, w2 in zip(first_plan.waves, subsequent_plan.waves):
                assert w1.wave_id == w2.wave_id
                assert len(w1.steps) == len(w2.steps)
                for s1, s2 in zip(w1.steps, w2.steps):
                    assert s1.capability_key == s2.capability_key

        # Non-negotiable assertion: ZERO LLM calls across all 100 runs
        for mock in mocks:
            mock.assert_not_called()


def test_planner_wave_structuring():
    """Verifies that independent capabilities run in wave 1, dependent in wave 2."""
    cap_req = CapabilitySpec(
        key="derive_requirements",
        kind=CapabilityKind.DERIVATION,
        name="Derive Requirements",
        inputs={"types": ["Goal"]},
        outputs={"types": ["Requirement"]},
    )
    cap_risk = CapabilitySpec(
        key="derive_risks",
        kind=CapabilityKind.DERIVATION,
        name="Derive Risks",
        inputs={"types": ["Goal"]},
        outputs={"types": ["Risk"]},
    )
    cap_arch = CapabilitySpec(
        key="project_architecture",
        kind=CapabilityKind.PROJECTION,
        name="Project Architecture",
        inputs={"types": ["Requirement", "Risk"]},
        outputs={"types": ["SystemArchitecture"]},
    )

    graph_state = {"available_types": ["Goal"]}

    plan = build_execution_plan(
        target_outputs=["SystemArchitecture"],
        available_capabilities=[cap_req, cap_risk, cap_arch],
        graph_state=graph_state,
    )

    assert plan.status == PlanStatus.READY
    assert len(plan.waves) == 2

    wave1_keys = [s.capability_key for s in plan.waves[0].steps]
    wave2_keys = [s.capability_key for s in plan.waves[1].steps]

    # Wave 1 contains independent derivations
    assert set(wave1_keys) == {"derive_requirements", "derive_risks"}
    # Wave 2 contains projection dependent on wave 1 outputs
    assert wave2_keys == ["project_architecture"]


def test_planner_thrash_guard():
    """Verifies cap re-attempts are capped at < 2 per capability."""
    cap_derive_req = CapabilitySpec(
        key="derive_requirements",
        kind=CapabilityKind.DERIVATION,
        name="Derive Requirements",
        inputs={"types": ["Goal"]},
        outputs={"types": ["Requirement"]},
    )
    graph_state = {"available_types": ["Goal"]}

    # Attempt count is 1 (< 2) -> Allowed
    plan1 = replan_next_wave(
        target_outputs=["Requirement"],
        available_capabilities=[cap_derive_req],
        current_graph_state=graph_state,
        attempt_history={"derive_requirements": 1},
    )
    assert plan1.status == PlanStatus.READY

    # Attempt count is 2 (>= 2) -> Excluded by thrash guard -> UNSATISFIABLE
    plan2 = replan_next_wave(
        target_outputs=["Requirement"],
        available_capabilities=[cap_derive_req],
        current_graph_state=graph_state,
        attempt_history={"derive_requirements": 2},
    )
    assert plan2.status == PlanStatus.UNSATISFIABLE
    assert "Requirement" in plan2.missing_inputs


def test_planner_single_producer_rule():
    """Verifies single-producer-per-type rule selects exactly one capability per type."""
    cap_a = CapabilitySpec(
        key="derive_req_a",
        kind=CapabilityKind.DERIVATION,
        name="Derive Req A",
        inputs={"types": ["Goal"]},
        outputs={"types": ["Requirement"]},
    )
    cap_b = CapabilitySpec(
        key="derive_req_b",
        kind=CapabilityKind.DERIVATION,
        name="Derive Req B",
        inputs={"types": ["Goal"]},
        outputs={"types": ["Requirement"]},
    )

    graph_state = {"available_types": ["Goal"]}

    plan = build_execution_plan(
        target_outputs=["Requirement"],
        available_capabilities=[cap_a, cap_b],
        graph_state=graph_state,
    )

    assert plan.status == PlanStatus.READY
    # Single producer chosen
    all_steps = [s for w in plan.waves for s in w.steps]
    assert len(all_steps) == 1
    assert all_steps[0].capability_key == "derive_req_a"
