"""Structured, evidence-backed BA requirement contracts and pure projections."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequirementCategory(str, Enum):
    BUSINESS = "business"
    STAKEHOLDER = "stakeholder"
    FUNCTIONAL = "functional"
    NONFUNCTIONAL = "nonfunctional"
    TRANSITIONAL = "transitional"


class RequirementType(str, Enum):
    """More specific classification for the requirement register and document split."""

    BUSINESS = "business"
    STAKEHOLDER = "stakeholder"
    FUNCTIONAL = "functional"
    NONFUNCTIONAL = "nonfunctional"
    BUSINESS_RULE = "business_rule"
    CONSTRAINT = "constraint"
    ASSUMPTION = "assumption"
    ARCHITECTURE_DECISION = "architecture_decision"
    TRANSITIONAL = "transitional"


class SourceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    ordinal: int = Field(ge=0)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    text: str = Field(min_length=1)
    speaker: str | None = None
    timestamp: str | None = None

    @field_validator("end_char")
    @classmethod
    def end_must_not_precede_start(cls, end_char: int, info) -> int:
        start_char = info.data.get("start_char")
        if start_char is not None and end_char < start_char:
            raise ValueError("end_char must not precede start_char")
        return end_char


class RequirementAmbiguity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    question: str | None = None


class RequirementConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    value: str = Field(min_length=1)
    measurable: bool = False


class ExtractedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_key: str = Field(min_length=1)
    category: RequirementCategory
    requirement_type: RequirementType | None = None
    functional_area: str | None = None
    stakeholder: str | None = None
    owner: str | None = None
    task: str | None = None
    object: str | None = None
    benefit: str | None = None
    business_problem: str | None = None
    business_objective: str | None = None
    success_metric: str | None = None
    priority: str | None = None
    status: str | None = None
    trigger: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    input_data: list[str] = Field(default_factory=list)
    output_data: list[str] = Field(default_factory=list)
    data_format: str | None = None
    exceptions: list[str] = Field(default_factory=list)
    comments: str | None = None
    constraints: list[RequirementConstraint] = Field(default_factory=list)
    ambiguities: list[RequirementAmbiguity] = Field(default_factory=list)
    evidence_span_ids: list[str] = Field(min_length=1)

    def missing_fields(self) -> list[str]:
        missing = [
            field
            for field, value in (
                ("benefit", self.benefit),
                ("stakeholder", self.stakeholder),
                ("task", self.task),
                ("trigger", self.trigger),
            )
            if not value
        ]
        if not self.outcomes:
            missing.append("outcomes")
        return sorted(missing)


class RequirementExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_spans: list[SourceSpan] = Field(default_factory=list)
    requirements: list[ExtractedRequirement] = Field(default_factory=list)


def _value(value: str | None, placeholder: str) -> str:
    return value or f"<UNSPECIFIED: {placeholder}>"


def render_user_story(requirement: ExtractedRequirement) -> str:
    return (
        f"As a {_value(requirement.stakeholder, 'stakeholder')}, "
        f"I want to {_value(requirement.task, 'task')}, "
        f"so that {_value(requirement.benefit, 'benefit')}."
    )


def render_gherkin(requirement: ExtractedRequirement) -> str:
    precondition = requirement.preconditions[0] if requirement.preconditions else "<UNSPECIFIED: precondition>"
    outcome = requirement.outcomes[0] if requirement.outcomes else "<UNSPECIFIED: outcome>"
    return f"Given {precondition}, when {_value(requirement.trigger, 'trigger')}, then {outcome}."
