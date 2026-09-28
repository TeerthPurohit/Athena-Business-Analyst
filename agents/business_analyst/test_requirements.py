import pytest

from agents.business_analyst.requirements import (
    ExtractedRequirement,
    RequirementCategory,
    render_gherkin,
    render_user_story,
)


def test_functional_requirement_projects_story_and_gherkin():
    requirement = ExtractedRequirement(
        external_key="inventory-approval",
        category=RequirementCategory.FUNCTIONAL,
        stakeholder="warehouse manager",
        task="approve stock adjustments",
        object="inventory adjustment",
        benefit="inventory remains accurate",
        trigger="an adjustment is submitted",
        preconditions=["the manager is authenticated"],
        outcomes=["the adjustment is recorded"],
        evidence_span_ids=["span-1"],
    )

    assert render_user_story(requirement) == (
        "As a warehouse manager, I want to approve stock adjustments, "
        "so that inventory remains accurate."
    )
    assert render_gherkin(requirement) == (
        "Given the manager is authenticated, when an adjustment is submitted, "
        "then the adjustment is recorded."
    )


def test_missing_outcome_is_reported_not_invented():
    requirement = ExtractedRequirement(
        external_key="inventory-approval",
        category=RequirementCategory.FUNCTIONAL,
        stakeholder="warehouse manager",
        task="approve stock adjustments",
        evidence_span_ids=["span-1"],
    )

    assert requirement.missing_fields() == ["benefit", "outcomes", "trigger"]
    assert "<UNSPECIFIED: outcome>" in render_gherkin(requirement)


@pytest.mark.parametrize(
    "category",
    [
        RequirementCategory.BUSINESS,
        RequirementCategory.STAKEHOLDER,
        RequirementCategory.FUNCTIONAL,
        RequirementCategory.NONFUNCTIONAL,
        RequirementCategory.TRANSITIONAL,
    ],
)
def test_requirement_category_is_limited_to_business_analysis_taxonomy(category):
    requirement = ExtractedRequirement(
        external_key="req", category=category, evidence_span_ids=["span-1"]
    )

    assert requirement.category is category
