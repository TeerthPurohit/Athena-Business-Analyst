"""Project IR: the Semantic Planner's only output (§3, §4, §6.3 point 2).

CLAUDE.md Non-Negotiables:
- Pydantic models MUST have extra="forbid" on every schema.
- Semantic Planner emits ProjectIR ONLY — no execution ordering, no capability names.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from agents.business_analyst.business_context import empty_business_context


class Objective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Objective ID e.g. obj-1")
    description: str = Field(description="Business objective description")
    category: Optional[str] = Field(default="primary", description="Objective category e.g. primary, compliance, secondary")
    priority: Optional[str] = Field(default="high", description="Priority level e.g. high, medium, low")


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Domain entity name e.g. Nurse, InventoryBatch")
    type: str = Field(description="Entity type e.g. Actor, Resource, System")
    attributes: List[str] = Field(default_factory=list, description="Known entity attributes")


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Goal ID e.g. goal-1")
    name: str = Field(description="Goal name")
    description: str = Field(description="Goal detail")
    target_metrics: List[str] = Field(default_factory=list, description="Target metrics or success criteria")


class ProjectScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    in_scope: List[str] = Field(default_factory=list, description="Features and boundaries in scope")
    out_of_scope: List[str] = Field(default_factory=list, description="Boundaries explicitly out of scope")
    constraints: List[str] = Field(default_factory=list, description="Business or technical constraints")


class ProjectIR(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str = Field(description="Name of the project")
    objectives: List[Objective] = Field(default_factory=list, description="Key business objectives")
    entities: List[Entity] = Field(default_factory=list, description="Domain entities identified")
    goals: List[Goal] = Field(default_factory=list, description="Target project goals")
    scope: ProjectScope = Field(default_factory=ProjectScope, description="Project scope boundaries")
    decisions: List[str] = Field(default_factory=list, description="Decisions made or implied by the request (e.g. 'Use JWT for auth')")
    business_rules: List[str] = Field(default_factory=list, description="Business rules/policies the system must enforce")
    assumptions: List[str] = Field(default_factory=list, description="Assumptions made where the request was ambiguous")
    open_questions: List[str] = Field(default_factory=list, description="Open questions the stakeholder still needs to answer")
    business_context: Dict[str, Any] = Field(
        default_factory=empty_business_context,
        description="Evidence-backed values in Athena's canonical business context structure; entity sections are lists of records; unknown fields are omitted",
    )
