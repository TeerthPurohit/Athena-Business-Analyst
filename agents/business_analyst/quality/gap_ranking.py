"""Gap Ranking Engine for BA OS Smart Clarification (Feature 3).

Pure module — ZERO LLM calls. Tests assert this explicitly.

Implements the Gap Ranking Engine specified in architecture_plan.md §2.3:
  coverage_gain(gap, nodes, edges) -> float
  rank_gaps(ambiguities, nodes, edges) -> list[dict]
  detect_value_conflict(new_answer, prior_answer_facts) -> dict | None

The ranking formula is:
  coverage_gain = Σ (1 - completeness(n)) * fan_out(n) * criticality(n) / cost(gap)

where:
  completeness(n) = 1 - (ambiguity_count(n) / max(1, total_fields(n)))
  fan_out(n)      = count of outgoing BaEdges for node_id
  criticality(n)  = fixed dict keyed by node_type (no DB config — rule of three)
  cost(gap)       = flat 1.0 (no evidence for differentiated costs)
"""
from typing import Any, Dict, List, Optional, Tuple

from agents.business_analyst.quality.similarity import find_knn_contradiction_candidates


# Fixed criticality weights — revisit only when a second real need appears (rule of three)
_CRITICALITY: Dict[str, float] = {
    "Requirement": 1.0,
    "NFR": 0.9,
    "BusinessRule": 0.85,
    "Entity": 0.8,
    "ProcessStep": 0.75,
    "Risk": 0.7,
    "Stakeholder": 0.6,
    "GlossaryTerm": 0.5,
}
_DEFAULT_CRITICALITY: float = 0.5
_GAP_COST: float = 1.0  # flat — no evidence for differentiated costs


def _completeness(node_id: str, ambiguities_by_node: Dict[str, int], total_fields_by_node: Dict[str, int]) -> float:
    """Returns fraction of fields that are NOT ambiguous for a node. Range [0, 1]."""
    total = max(1, total_fields_by_node.get(node_id, 1))
    ambiguous = ambiguities_by_node.get(node_id, 0)
    return 1.0 - min(1.0, ambiguous / total)


def _fan_out(node_id: str, edges: List[Dict[str, Any]]) -> int:
    """Count of outgoing edges from node_id."""
    return sum(1 for e in edges if e.get("source_id") == node_id or e.get("from_id") == node_id)


def coverage_gain(
    gap: Dict[str, Any],
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    ambiguities_by_node: Optional[Dict[str, int]] = None,
    total_fields_by_node: Optional[Dict[str, int]] = None,
) -> float:
    """Computes coverage_gain for a single gap.

    Σ over nodes n unblocked by this gap:
      (1 - completeness(n)) * fan_out(n) * criticality(n) / cost(gap)

    Args:
        gap: A single entry from detect_ambiguities() output.
        nodes: List of graph node dicts (each with 'id', 'type', 'attrs').
        edges: List of BaEdge-like dicts (each with 'source_id' or 'from_id').
        ambiguities_by_node: Pre-computed {node_id: count}; computed here if None.
        total_fields_by_node: Pre-computed {node_id: count}; computed here if None.
    """
    gap_node_id = gap.get("node_id")

    # Build lookup tables if not supplied (avoids recomputing in rank_gaps inner loop)
    if ambiguities_by_node is None:
        ambiguities_by_node = {}
    if total_fields_by_node is None:
        total_fields_by_node = {
            n["id"]: max(1, len(n.get("attrs", {})))
            for n in nodes
            if n.get("id")
        }

    score = 0.0
    for node in nodes:
        node_id = node.get("id")
        if not node_id:
            continue
        # "Unblocked by gap" — node is affected by, or downstream of, the gap's node
        # Simple heuristic: include the gap's own node plus any node it has an outgoing edge to
        if node_id != gap_node_id:
            # Only count nodes reachable via one outgoing edge from the gap node
            connected = any(
                (e.get("source_id") == gap_node_id or e.get("from_id") == gap_node_id)
                and (e.get("target_id") == node_id or e.get("to_id") == node_id)
                for e in edges
            )
            if not connected:
                continue

        ntype = node.get("type", "")
        c = _completeness(node_id, ambiguities_by_node, total_fields_by_node)
        fo = _fan_out(node_id, edges)
        crit = _CRITICALITY.get(ntype, _DEFAULT_CRITICALITY)
        score += (1.0 - c) * fo * crit / _GAP_COST

    # Add base contribution from the gap node itself (even with no outgoing edges)
    if gap_node_id:
        for node in nodes:
            if node.get("id") == gap_node_id:
                ntype = node.get("type", "")
                c = _completeness(gap_node_id, ambiguities_by_node, total_fields_by_node)
                fo = _fan_out(gap_node_id, edges)
                crit = _CRITICALITY.get(ntype, _DEFAULT_CRITICALITY)
                # Base score: incompleteness * criticality (fan_out=1 minimum for the node itself)
                score += (1.0 - c) * max(1, fo) * crit / _GAP_COST
                break

    return score


def rank_gaps(
    ambiguities: List[Dict[str, Any]],
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Scores and sorts detect_ambiguities() output. Returns gaps highest-score-first.

    Zero LLM calls — purely deterministic. Test must assert mock_llm.assert_not_called().
    """
    if not ambiguities:
        return []

    # Pre-compute per-node ambiguity counts and total field counts once
    ambiguities_by_node: Dict[str, int] = {}
    for gap in ambiguities:
        nid = gap.get("node_id")
        if nid:
            ambiguities_by_node[nid] = ambiguities_by_node.get(nid, 0) + 1

    total_fields_by_node: Dict[str, int] = {
        n["id"]: max(1, len(n.get("attrs", {})))
        for n in nodes
        if n.get("id")
    }

    scored = []
    for gap in ambiguities:
        score = coverage_gain(
            gap, nodes, edges,
            ambiguities_by_node=ambiguities_by_node,
            total_fields_by_node=total_fields_by_node,
        )
        scored.append({**gap, "_score": score})

    scored.sort(key=lambda g: g["_score"], reverse=True)
    return scored


def detect_value_conflict(
    new_answer: str,
    prior_answer_facts: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Detects a conflict between new_answer and any prior recorded answer.

    Uses find_knn_contradiction_candidates() to gate on shared subject_key,
    then a deterministic value-inequality check — NEVER an LLM judgment.

    Returns a conflict dict {prior, new} or None if no conflict found.
    """
    if not prior_answer_facts or not new_answer:
        return None

    new_fact_like = {"subject_key": None, "value": {"answer": new_answer}}
    # Reuse shared-subject gating from similarity.py
    for prior in prior_answer_facts:
        prior_value = prior.get("value") or {}
        prior_answer = prior_value.get("answer", "")
        if not prior_answer:
            continue
        # Deterministic inequality — strip whitespace, case-insensitive
        if prior_answer.strip().lower() != new_answer.strip().lower():
            # Run kNN gate on shared subject_key to confirm these are comparable
            candidates = find_knn_contradiction_candidates(
                [
                    {"subject_key": prior.get("subject_key"), "value": prior_value},
                    {"subject_key": prior.get("subject_key"), "value": {"answer": new_answer}},
                ]
            )
            if candidates:
                return {"prior": prior_answer, "new": new_answer}

    return None
