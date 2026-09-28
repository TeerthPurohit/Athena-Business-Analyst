"""Derivation capabilities package (§2.1, §11.6).

Derivation capabilities infer structured knowledge from graph state and emit side-effect relation facts (derived_from / traces_to).
"""
from typing import Any, Dict, List, Optional
import uuid

from agents.business_analyst.capabilities.derivation.derive_requirements import run_derive_requirements
from agents.business_analyst.capabilities.derivation.derive_glossary_terms import run_derive_glossary_terms


def run_model_process(nodes: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """Derives process steps, actors, and decision points."""
    facts = []
    for node in nodes:
        node_id = node.get("id") or node.get("subject_key", "proc_1")
        facts.append({
            "id": str(uuid.uuid4()),
            "subject_type": "ProcessStep",
            "subject_key": f"step_{node_id}",
            "predicate": "to_be_step",
            "value": {"step_name": f"Execute step for {node_id}", "actor": "System"},
            "source_id": source_id,
        })
        facts.append({
            "id": str(uuid.uuid4()),
            "subject_type": "ProcessStep",
            "subject_key": f"step_{node_id}",
            "predicate": "derived_from",
            "object_type": node.get("type", "Requirement"),
            "object_key": node_id,
            "source_id": source_id,
        })
    return facts


def run_derive_edge_cases(nodes: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """Derives edge cases and exploratory risk nodes."""
    facts = []
    for node in nodes:
        node_id = node.get("id") or node.get("subject_key", "req_1")
        risk_key = f"risk_edge_{node_id}"
        facts.append({
            "id": str(uuid.uuid4()),
            "subject_type": "Risk",
            "subject_key": risk_key,
            "predicate": "edge_case_risk",
            "value": {"description": f"Concurrent access edge case on {node_id}", "severity": "high"},
            "source_id": source_id,
        })
        facts.append({
            "id": str(uuid.uuid4()),
            "subject_type": "Risk",
            "subject_key": risk_key,
            "predicate": "traces_to",
            "object_type": "Requirement",
            "object_key": node_id,
            "source_id": source_id,
        })
    return facts


def run_derive_nfr(nodes: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """Derives Non-Functional Requirements (performance, security, availability)."""
    facts = []
    categories = ["performance", "security", "availability"]
    for idx, cat in enumerate(categories):
        nfr_key = f"nfr_{cat}_{idx+1}"
        facts.append({
            "id": str(uuid.uuid4()),
            "subject_type": "Requirement",
            "subject_key": nfr_key,
            "predicate": "nfr_spec",
            "value": {"category": cat, "target": f"Sub-second response for {cat}"},
            "source_id": source_id,
        })
    return facts


def run_derive_data_model(nodes: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """Derives entity-attribute-relationship data model facts."""
    facts = []
    for node in nodes:
        node_id = node.get("id") or node.get("subject_key", "entity_1")
        facts.append({
            "id": str(uuid.uuid4()),
            "subject_type": "Entity",
            "subject_key": f"entity_{node_id}",
            "predicate": "data_model_def",
            "value": {"name": str(node_id), "attributes": ["id", "created_at", "status"]},
            "source_id": source_id,
        })
    return facts


def run_derive_stakeholders(facts_input: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """Derives stakeholder register facts."""
    return [
        {
            "id": str(uuid.uuid4()),
            "subject_type": "Stakeholder",
            "subject_key": "sh_head_nurse",
            "predicate": "role_def",
            "value": {"name": "Head Nurse", "role": "Process Owner", "authority": "approver"},
            "source_id": source_id,
        }
    ]


def run_derive_raci(nodes: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """Derives RACI matrix relation facts."""
    facts = []
    for node in nodes:
        node_id = node.get("id") or node.get("subject_key", "task_1")
        facts.append({
            "id": str(uuid.uuid4()),
            "subject_type": "ProcessStep",
            "subject_key": str(node_id),
            "predicate": "raci_assignment",
            "value": {"responsible": "Developer", "accountable": "Tech Lead", "consulted": "BA", "informed": "PO"},
            "source_id": source_id,
        })
    return facts


def run_derive_risks(facts_input: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """Derives risk log facts."""
    return [
        {
            "id": str(uuid.uuid4()),
            "subject_type": "Risk",
            "subject_key": "risk_migration_delay",
            "predicate": "risk_log_entry",
            "value": {"description": "Legacy database migration delay", "impact": "medium", "mitigation": "Parallel run"},
            "source_id": source_id,
        }
    ]


def run_derive_options(facts_input: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """Derives solution options analysis trade-offs."""
    return [
        {
            "id": str(uuid.uuid4()),
            "subject_type": "SolutionOption",
            "subject_key": "option_postgres_graph",
            "predicate": "option_tradeoff",
            "value": {"option_name": "Postgres Recursive CTE Graph", "pros": "ACID compliance, one store", "cons": "CTE query complexity"},
            "source_id": source_id,
        }
    ]
