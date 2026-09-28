"""No-DB tests for the business context structure, its deterministic fold, and chat replies."""
from types import SimpleNamespace

from agents.business_analyst.business_context import (
    BUSINESS_CONTEXT_STRUCTURE,
    ENTITY_KEY_FIELDS,
    fold_business_context,
    normalize_business_context,
    record_key,
)
from agents.business_analyst.ir import ProjectIR


def fact(section, subject_key=None, **value):
    return SimpleNamespace(subject_type="business_context", subject_key=subject_key or section, value={"section": section, **value})


def test_structure_has_all_35_sections_and_entity_keys_are_real_fields() -> None:
    assert len(BUSINESS_CONTEXT_STRUCTURE) == 35
    for section, keys in ENTITY_KEY_FIELDS.items():
        assert set(keys) <= set(BUSINESS_CONTEXT_STRUCTURE[section])


def test_normalize_makes_entity_sections_record_lists_and_matches_keys_case_insensitively() -> None:
    context = normalize_business_context({
        "stakeholders": [{"name": "Priya", "role": "Ops head"}, {"name": "Raj"}, {}],
        "processes": {"process_name": "Dispatch", "slas": "2h"},  # dict and lowercased key from a model
        "organization": {"company_name": "Acme"},
    })
    assert [record["name"] for record in context["stakeholders"]] == ["Priya", "Raj"]
    assert context["processes"][0]["SLAs"] == "2h"
    assert context["organization"]["company_name"] == "Acme"
    assert context["organization"]["industry"] is None
    assert context["risks"] == []


def test_fold_merges_records_by_key_scalars_latest_wins_lists_union() -> None:
    facts = [
        fact("organization", field="industry", value="Logistics"),
        fact("organization", field="industry", value="Freight logistics"),
        fact("organization", field="locations", value=["Pune"]),
        fact("organization", field="locations", value=["Pune", "Mumbai"]),
        fact("stakeholders", "stakeholders:priya", record={"name": "Priya", "role": "Ops head"}),
        fact("stakeholders", "stakeholders:priya", record={"name": "Priya", "authority": "Approves vendors"}),
        fact("stakeholders", "stakeholders:raj", record={"name": "Raj"}),
        fact("stakeholders", field="name", value="Legacy flat value"),  # pre-record facts still show
        SimpleNamespace(subject_type="goal", subject_key="g", value={"section": "organization"}),
    ]
    context = fold_business_context(facts)
    assert context["organization"]["industry"] == "Freight logistics"
    assert context["organization"]["locations"] == ["Pune", "Mumbai"]
    priya = next(record for record in context["stakeholders"] if record["name"] == "Priya")
    assert priya["role"] == "Ops head" and priya["authority"] == "Approves vendors"
    assert len(context["stakeholders"]) == 3


def test_record_key_is_stable_and_requires_the_key_field() -> None:
    assert record_key("stakeholders", {"name": "  Priya  Shah "}) == record_key("stakeholders", {"name": "priya shah"})
    assert record_key("integrations", {"source_system": "CRM", "target_system": "ERP"}) == "crm -> erp"
    assert record_key("stakeholders", {"role": "Ops head"}) is None


def test_record_reply_is_plain_language() -> None:
    from agents.business_analyst.api.routes import _record_reply

    ir = ProjectIR(project_name="x", decisions=["Use Tally"], business_context={"stakeholders": [{"name": "Priya"}]})
    reply = _record_reply({"facts_created": 3, "ir": ir})
    assert "1 decision" in reply and "Priya" in reply
    assert "fact" not in reply.lower() and "graph" not in reply.lower()
    assert "couldn't confirm" in _record_reply({"facts_created": 0, "ir": ir})
