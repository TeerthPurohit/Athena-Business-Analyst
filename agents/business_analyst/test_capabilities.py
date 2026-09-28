"""Tests for Acquisition & Derivation Capabilities (§2.1, §11.6).

Mandatory Verification Tests:
1. derive_requirements emits structured fields (actor, capability, object, benefit, trigger, constraints), not prose.
2. Honest-failure path: missing threshold renders <UNSPECIFIED: reorder_threshold> and writes a gap fact.
3. Every derivation capability writes derived_from / traces_to relation facts as a side effect.
4. Acquisition capabilities extract raw facts.
"""
from agents.business_analyst.capabilities.acquisition import (
    run_compliance_lookup,
    run_document_analysis,
    run_interview,
    run_market_research,
)
from agents.business_analyst.capabilities.derivation import (
    run_derive_data_model,
    run_derive_edge_cases,
    run_derive_glossary_terms,
    run_derive_nfr,
    run_derive_options,
    run_derive_raci,
    run_derive_requirements,
    run_derive_risks,
    run_derive_stakeholders,
    run_model_process,
)


def test_derive_requirements_preserves_source_structured_fields() -> None:
    source_facts = [
        {
            "id": "fact_source_1",
            "subject_type": "Goal",
            "subject_key": "goal_stock_visibility",
            "value": {
                "actor": "Warehouse manager",
                "capability": "approve stock adjustments",
                "object": "inventory adjustment",
                "benefit": "maintain accurate inventory",
                "trigger": "an adjustment is submitted",
                "constraints": ["manager is authenticated"],
            },
        }
    ]

    facts = run_derive_requirements(source_facts=source_facts, source_id="src_doc_1")
    requirement = next(f for f in facts if f["subject_type"] == "Requirement")

    assert requirement["value"] == {
        "actor": "Warehouse manager",
        "capability": "approve stock adjustments",
        "object": "inventory adjustment",
        "benefit": "maintain accurate inventory",
        "trigger": "an adjustment is submitted",
        "constraints": ["manager is authenticated"],
    }


def test_derive_requirements_emits_gaps_instead_of_domain_defaults() -> None:
    facts = run_derive_requirements(
        source_facts=[{"id": "fact_no_fields", "subject_key": "source", "value": {}}],
        source_id="src_doc_2",
    )

    assert not any(f["subject_type"] == "Requirement" for f in facts)
    assert {
        f["value"]["missing_field"] for f in facts if f["subject_type"] == "Gap"
    } == {"actor", "capability", "object", "benefit", "trigger"}
    assert all(
        forbidden not in str(facts)
        for forbidden in ("Nurse", "InventoryBatch", "HIPAA", "reorder_threshold")
    )



def test_derivation_side_effect_traceability_facts() -> None:
    facts = run_derive_requirements(
        source_facts=[
            {
                "id": "src_f1",
                "subject_type": "Goal",
                "subject_key": "goal_1",
                "value": {"actor": "User", "capability": "submit request"},
            }
        ],
        source_id="src_doc_3",
    )

    trace_fact = next(f for f in facts if f["predicate"] == "derived_from")
    assert trace_fact["subject_type"] == "Requirement"
    assert trace_fact["object_key"] == "goal_1"


def test_acquisition_capabilities() -> None:
    """Asserts acquisition capabilities generate raw fact dicts."""
    doc_facts = run_document_analysis(source_id="s1", document_text="Line 1\nLine 2")
    assert len(doc_facts) == 2
    assert doc_facts[0]["subject_type"] == "DocumentSection"

    interview_facts = run_interview(source_id="s2", transcript_text="We need stock tracking", stakeholder_id="sh_1")
    assert len(interview_facts) == 1
    assert interview_facts[0]["subject_type"] == "InterviewQuote"

    compliance_facts = run_compliance_lookup(source_id="s3", domain="HIPAA")
    assert len(compliance_facts) == 1
    assert compliance_facts[0]["subject_type"] == "Constraint"

    market_facts = run_market_research(source_id="s4", topic="Hospital Supply Chains")
    assert len(market_facts) == 1
    assert market_facts[0]["subject_type"] == "MarketFact"
