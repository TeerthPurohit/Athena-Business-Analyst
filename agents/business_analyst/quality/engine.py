"""Quality Engine Orchestrator (§5, §9).

CLAUDE.md Non-Negotiables:
- Deterministic score calculation is authoritative and system of record.
- llm_assessment stored separately, advisory ONLY — can raise a review task, NEVER changes official evidence_confidence.
"""
from typing import Any, Dict, List, Optional

from agents.business_analyst.quality.ambiguity import detect_ambiguities
from agents.business_analyst.quality.dimensions import calculate_quality_dimensions
from agents.business_analyst.quality.scoring import calculate_evidence_confidence
from agents.business_analyst.quality.similarity import PairFingerprintCache, find_knn_contradiction_candidates


class QualityEngine:
    """Orchestrates deterministic quality scoring and advisory LLM evaluation for BA OS."""

    def __init__(self) -> None:
        self.fingerprint_cache = PairFingerprintCache()

    def score_fact(
        self,
        fact: Dict[str, Any],
        sources: List[Dict[str, Any]],
        contradictions_count: int = 0,
    ) -> float:
        """Calculates authoritative deterministic evidence confidence for a fact."""
        return calculate_evidence_confidence(
            fact=fact,
            sources=sources,
            contradictions_count=contradictions_count,
        )

    def evaluate_graph_quality(
        self,
        facts: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        contradictions: List[Dict[str, Any]],
        ontology: Dict[str, Any],
        llm_feedback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluates overall graph state quality.

        Returns:
        - fact_scores: Dict[fact_id, float]
        - dimensions: Dict[dimension_name, float]
        - ambiguities: List[Dict]
        - llm_assessment: Dict (advisory only)
        - review_tasks_raised: List[Dict]
        """
        # Map sources by source_id
        source_map: Dict[str, Dict[str, Any]] = {}
        for s in sources:
            s_id = s.get("id") or s.get("source_id")
            if s_id:
                source_map[s_id] = s

        # Score every fact deterministically
        fact_scores: Dict[str, float] = {}
        for fact in facts:
            fact_id = fact.get("id", "unknown")
            s_id = fact.get("source_id")
            fact_sources = [source_map[s_id]] if s_id in source_map else sources

            # Count contradictions involving this fact
            c_count = sum(
                1 for c in contradictions
                if c.get("fact_id_a") == fact_id or c.get("fact_id_b") == fact_id
            )

            fact_scores[fact_id] = calculate_evidence_confidence(
                fact=fact,
                sources=fact_sources,
                contradictions_count=c_count,
            )

        # Calculate 5 quality dimensions
        dims = calculate_quality_dimensions(
            nodes=nodes,
            edges=edges,
            contradictions=contradictions,
            ontology=ontology,
        )

        # Detect structural ambiguities
        ambiguities = detect_ambiguities(nodes)

        # Handle advisory LLM assessment (stored separately, can raise review tasks, NEVER alters fact_scores or evidence_confidence)
        llm_assessment = {}
        review_tasks_raised = []

        if llm_feedback:
            llm_assessment = {
                "summary": llm_feedback.get("summary", ""),
                "suggested_reviews": llm_feedback.get("suggested_reviews", []),
                "advisory_score": llm_feedback.get("advisory_score", 0.5),
            }

            for review in llm_feedback.get("suggested_reviews", []):
                review_tasks_raised.append({
                    "task_type": "human_review",
                    "reason": review.get("reason", "LLM advisory review raised"),
                    "target_id": review.get("target_id"),
                    "source": "llm_assessment",
                })

        return {
            "fact_scores": fact_scores,
            "dimensions": dims,
            "ambiguities": ambiguities,
            "llm_assessment": llm_assessment,
            "review_tasks_raised": review_tasks_raised,
        }
