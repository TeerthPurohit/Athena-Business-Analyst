"""derive_requirements derivation capability (§2.1, §11.6).

CLAUDE.md Non-Negotiable Load-Bearing Prerequisite:
- Emits STRUCTURED FIELDS, not prose: {actor, capability, object, benefit, trigger, constraints[]}.
- Emits derived_from relation facts pointing requirement back to source fact/goal as a side effect.
- Honest-failure path: when threshold missing, renders <UNSPECIFIED: threshold_name> and writes a gap fact.
"""
from typing import Any, Dict, List, Optional
import uuid


def run_derive_requirements(
    source_facts: List[Dict[str, Any]],
    source_id: str,
) -> List[Dict[str, Any]]:
    """Preserves source-provided requirement fields and exposes every absence as a gap."""
    derived_facts: List[Dict[str, Any]] = []
    for fact in source_facts:
        value = fact.get("value")
        if not isinstance(value, dict):
            value = {}
        req_key = f"req_{fact.get('subject_key', 'source')}_{uuid.uuid4().hex[:6]}"
        fields = {
            "actor": value.get("actor") or value.get("stakeholder"),
            "capability": value.get("capability") or value.get("task"),
            "object": value.get("object"),
            "benefit": value.get("benefit"),
            "trigger": value.get("trigger"),
        }
        for field, field_value in fields.items():
            if field_value:
                continue
            derived_facts.append(
                {
                    "id": str(uuid.uuid4()),
                    "subject_type": "Gap",
                    "subject_key": f"{req_key}:{field}",
                    "predicate": "missing_information",
                    "value": {
                        "requirement_key": req_key,
                        "missing_field": field,
                        "description": f"Source evidence does not specify {field}.",
                    },
                    "source_id": source_id,
                }
            )

        if fields["actor"] and fields["capability"]:
            derived_facts.append(
                {
                    "id": str(uuid.uuid4()),
                    "subject_type": "Requirement",
                    "subject_key": req_key,
                    "predicate": "to_be_spec",
                    "value": {
                        **fields,
                        "constraints": value.get("constraints", []),
                    },
                    "source_id": source_id,
                }
            )
            if fact.get("id"):
                derived_facts.append(
                    {
                        "id": str(uuid.uuid4()),
                        "subject_type": "Requirement",
                        "subject_key": req_key,
                        "predicate": "derived_from",
                        "object_type": fact.get("subject_type", "SourceFact"),
                        "object_key": fact.get("subject_key", fact["id"]),
                        "source_id": source_id,
                    }
                )
    return derived_facts
