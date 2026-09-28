"""Tests for Semantic Planner (§3, §4, §8).

Mandatory Verification Tests:
1. Parse natural language request into ProjectIR.
2. ProjectIR schema enforces extra="forbid".
3. build_project_ir_llm calls agents.shared.llm_client.get_structured_output with agent_id="9".
4. On LLM failure/unconfigured, build_project_ir_llm logs BA_SEMANTIC_PLANNER_DEGRADED and raises —
   never a fabricated IR (it would be judged against the same text and could be persisted).
"""
import logging
from unittest.mock import AsyncMock, patch
import pytest

from agents.business_analyst.ir import Entity, Goal, Objective, ProjectIR, ProjectScope
from agents.business_analyst.semantic_planner import (
    build_project_ir_from_dict,
    build_project_ir_llm,
    logger as planner_logger,
)


@pytest.mark.asyncio
async def test_empty_request_returns_empty_ir_without_llm() -> None:
    with patch("agents.business_analyst.semantic_planner.llm_get_structured_output", new_callable=AsyncMock) as mock_llm_call:
        ir = await build_project_ir_llm("   ")
    assert ir.objectives == [] and ir.entities == []
    mock_llm_call.assert_not_called()


def test_project_ir_schema_extra_forbid() -> None:
    valid_data = {
        "project_name": "Test Project",
        "objectives": [
            {
                "id": "obj-1",
                "description": "Streamline checkout flow",
                "category": "business",
                "priority": "high",
            }
        ],
        "entities": [
            {
                "type": "Actor",
                "name": "Customer Account",
                "attributes": ["id", "email"],
            }
        ],
        "goals": [],
        "scope": {
            "in_scope": ["Checkout UI"],
            "out_of_scope": ["Inventory management"],
            "constraints": ["PCI DSS compliance"],
        },
    }

    ir = build_project_ir_from_dict(valid_data)
    assert ir.project_name == "Test Project"

    invalid_data = valid_data.copy()
    invalid_data["unallowed_extra_field"] = "should fail extra=forbid validation"

    with pytest.raises(ValueError):
        build_project_ir_from_dict(invalid_data)


@pytest.mark.asyncio
async def test_build_project_ir_llm_wiring() -> None:
    mock_prompt = "You are a Business Analyst OS Semantic Planner..."
    mock_ir = ProjectIR(
        project_name="LLM Parsed System",
        objectives=[Objective(id="obj-1", description="LLM Goal", category="primary", priority="high")],
        entities=[Entity(type="Actor", name="Physician", attributes=["id"])],
        goals=[],
        scope=ProjectScope(in_scope=["Triage"], out_of_scope=[], constraints=[]),
    )

    with patch("agents.business_analyst.semantic_planner.fetch_prompt", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_prompt

        with patch("agents.business_analyst.semantic_planner.llm_get_structured_output", new_callable=AsyncMock) as mock_llm_call:
            mock_llm_call.return_value = mock_ir

            res = await build_project_ir_llm("Build emergency triage app", session_id="sess_123")

            mock_fetch.assert_called_once_with("9", "ba_semantic_planner_v2")
            mock_llm_call.assert_called_once_with(
                system_prompt=mock_prompt,
                user_prompt="Build emergency triage app",
                response_model=ProjectIR,
                agent_id="9",
                name="parse-project-context",
                session_id="sess_123",
            )

            assert res.project_name == "LLM Parsed System"
            assert res.entities[0].type == "Actor"


@pytest.mark.asyncio
async def test_build_project_ir_llm_degraded_logs_warning_and_raises() -> None:
    """On LLM failure: BA_SEMANTIC_PLANNER_DEGRADED is logged and no fabricated IR is returned."""
    with patch("agents.business_analyst.semantic_planner.fetch_prompt", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = RuntimeError("API key missing")

        with patch.object(planner_logger, "warning") as mock_warn:
            with pytest.raises(RuntimeError):
                await build_project_ir_llm("Build supply chain app", session_id="sess_456")

            mock_warn.assert_called_once()
            assert "BA_SEMANTIC_PLANNER_DEGRADED" in mock_warn.call_args[0][0]
