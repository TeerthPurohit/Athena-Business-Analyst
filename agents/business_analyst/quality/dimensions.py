"""Deterministic Graph Quality Dimensions (§5, §9).

Evaluates knowledge graph quality across five dimensions:
- Completeness
- Consistency
- Traceability
- Ambiguity
- Risk
"""
from typing import Any, Dict, List, Set


def evaluate_completeness(nodes: List[Dict[str, Any]], ontology: Dict[str, Any]) -> float:
    """Calculates graph completeness ratio of populated required attributes against ontology."""
    if not nodes:
        return 1.0

    total_required = 0
    total_populated = 0

    type_schemas = ontology.get("types", {})

    for node in nodes:
        ntype = node.get("type", "")
        schema = type_schemas.get(ntype, {})
        req_fields = schema.get("required_attributes", [])
        attrs = node.get("attrs", {})

        if not req_fields:
            # If schema defines no explicit required fields, count populated non-null attrs
            total_required += 1
            if attrs:
                total_populated += 1
        else:
            total_required += len(req_fields)
            for f in req_fields:
                val = attrs.get(f)
                if val is not None and val != "" and not str(val).startswith("<UNSPECIFIED:"):
                    total_populated += 1

    if total_required == 0:
        return 1.0
    return round(total_populated / total_required, 4)


def evaluate_consistency(nodes: List[Dict[str, Any]], contradictions: List[Dict[str, Any]]) -> float:
    """Calculates graph consistency ratio based on active contradictions."""
    if not nodes:
        return 1.0

    contradicted_node_ids: Set[str] = set()
    for c in contradictions:
        if c.get("node_id_a"):
            contradicted_node_ids.add(c["node_id_a"])
        if c.get("node_id_b"):
            contradicted_node_ids.add(c["node_id_b"])

    consistent_count = sum(1 for n in nodes if n.get("id") not in contradicted_node_ids)
    return round(consistent_count / len(nodes), 4)


def evaluate_traceability(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> float:
    """Calculates traceability ratio of requirement nodes linked to source facts via derived_from/traces_to."""
    req_nodes = [n for n in nodes if n.get("type") in ("Requirement", "Goal", "Risk")]
    if not req_nodes:
        return 1.0

    traced_ids: Set[str] = set()
    for edge in edges:
        rel = str(edge.get("relation", "")).lower()
        if rel in ("derived_from", "traces_to", "validates", "references"):
            traced_ids.add(edge.get("source_id"))
            traced_ids.add(edge.get("target_id"))

    traced_count = sum(1 for n in req_nodes if n.get("id") in traced_ids or n.get("source_id"))
    return round(traced_count / len(req_nodes), 4)


def evaluate_ambiguity(nodes: List[Dict[str, Any]]) -> float:
    """Calculates ambiguity ratio (proportion of nodes without UNSPECIFIED placeholders)."""
    if not nodes:
        return 1.0

    unambiguous_count = 0
    for node in nodes:
        attrs = node.get("attrs", {})
        has_unspecified = any(
            isinstance(v, str) and v.startswith("<UNSPECIFIED:")
            for v in attrs.values()
        )
        if not has_unspecified:
            unambiguous_count += 1

    return round(unambiguous_count / len(nodes), 4)


def evaluate_risk(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> float:
    """Calculates risk score (1.0 - ratio of unmitigated high-criticality risks)."""
    risk_nodes = [n for n in nodes if n.get("type") == "Risk"]
    if not risk_nodes:
        return 1.0

    mitigated_risk_ids: Set[str] = set()
    for edge in edges:
        rel = str(edge.get("relation", "")).lower()
        if rel in ("mitigates", "validates", "addresses"):
            mitigated_risk_ids.add(edge.get("target_id"))

    mitigated_count = sum(1 for r in risk_nodes if r.get("id") in mitigated_risk_ids)
    return round(mitigated_count / len(risk_nodes), 4)


def calculate_quality_dimensions(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    contradictions: List[Dict[str, Any]],
    ontology: Dict[str, Any],
) -> Dict[str, float]:
    """Evaluates all five quality dimensions for a graph."""
    return {
        "completeness": evaluate_completeness(nodes, ontology),
        "consistency": evaluate_consistency(nodes, contradictions),
        "traceability": evaluate_traceability(nodes, edges),
        "ambiguity": evaluate_ambiguity(nodes),
        "risk": evaluate_risk(nodes, edges),
    }
