"""Deterministic Evidence Confidence Scoring (§5, §9).

CLAUDE.md Non-Negotiables:
- Completely deterministic — never calls an LLM.
- Free-text prompt injection immunity: scorer NEVER reads free text to decide score. Reads structural fields only.
- Decay rule applies ONLY to present-state (as-is) predicates, NEVER to-be (decision/definition) predicates.
- Pure LLM inference sources capped at 0.1000 score.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Load scoring configuration
CONFIG_PATH = Path(__file__).parent / "config" / "ba_scoring_v1.json"


def load_scoring_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_CONFIG = load_scoring_config()
_CONSTANTS = _CONFIG["constants"]

K_CORROBORATION = float(_CONSTANTS["k_corroboration"])
CONTRADICTION_PENALTY = float(_CONSTANTS["contradiction_penalty"])
LLM_SOURCE_CAP = float(_CONSTANTS["llm_source_cap"])
LLM_INFERENCE_ONLY_MAX_SCORE = float(_CONSTANTS["llm_inference_only_max_score"])
DEFAULT_HALF_LIFE_DAYS = float(_CONSTANTS.get("default_half_life_days", 180))
PER_TIER_CEILINGS = _CONSTANTS["per_tier_ceilings"]


def get_tier_ceiling(tier: str) -> float:
    tier_clean = str(tier).lower().strip()
    return float(PER_TIER_CEILINGS.get(tier_clean, PER_TIER_CEILINGS.get("tier10", 0.40)))


def calculate_evidence_confidence(
    fact: Dict[str, Any],
    sources: List[Dict[str, Any]],
    contradictions_count: int = 0,
    current_time: Optional[datetime] = None,
) -> float:
    """Calculates deterministic evidence confidence score for a fact.

    Inputs are strictly structural (source kind/tier, source IDs, human_approval, predicate_kind).
    Ignores free-text values entirely.
    """
    if not sources:
        return 0.1000

    # Check for human approval override
    if fact.get("human_approval") is True:
        # Human approval sets baseline high confidence
        base_score = 0.9650
        if contradictions_count > 0:
            base_score = base_score * ((1.0 - CONTRADICTION_PENALTY) ** contradictions_count)
        return round(base_score, 4)

    # Filter distinct sources by ID
    distinct_sources: Dict[str, Dict[str, Any]] = {}
    for s in sources:
        s_id = str(s.get("id") or s.get("source_id") or "unknown")
        if s_id not in distinct_sources:
            distinct_sources[s_id] = s

    source_list = list(distinct_sources.values())

    # Categorize sources: non-LLM vs LLM
    non_llm_sources = [s for s in source_list if str(s.get("kind", "")).lower() != "llm_inference"]
    llm_sources = [s for s in source_list if str(s.get("kind", "")).lower() == "llm_inference"]

    # Rule: If ONLY llm_inference sources exist, score is 0.1000 exactly
    if not non_llm_sources and llm_sources:
        return LLM_INFERENCE_ONLY_MAX_SCORE

    # Determine highest tier ceiling among non-LLM sources
    highest_ceiling = 0.40
    for s in non_llm_sources:
        tier_val = s.get("tier", "tier10")
        c = get_tier_ceiling(str(tier_val))
        if c > highest_ceiling:
            highest_ceiling = c

    # Calculate effective independent source count N_eff
    # Non-LLM distinct sources count as 1.0 each
    n_eff = float(len(non_llm_sources))
    # LLM sources capped at LLM_SOURCE_CAP total (e.g. 1.0)
    if llm_sources:
        n_eff += min(LLM_SOURCE_CAP, float(len(llm_sources)) * 0.2)

    # Corroboration formula: score = ceiling * (1 - e^(-k * N_eff))
    raw_score = highest_ceiling * (1.0 - math.exp(-K_CORROBORATION * n_eff))

    # Apply contradiction penalties
    if contradictions_count > 0:
        raw_score = raw_score * ((1.0 - CONTRADICTION_PENALTY) ** contradictions_count)

    # Apply decay rule ONLY to present-state (as-is) predicates
    predicate_kind = str(fact.get("predicate_kind") or "").lower()
    predicate_name = str(fact.get("predicate") or "").lower()
    is_as_is = (
        predicate_kind == "as_is"
        or predicate_name.startswith("as_is_")
        or predicate_name.startswith("asis_")
    )

    if is_as_is and fact.get("asserted_at"):
        asserted_at = fact["asserted_at"]
        if isinstance(asserted_at, str):
            try:
                asserted_at = datetime.fromisoformat(asserted_at.replace("Z", "+00:00"))
            except ValueError:
                asserted_at = None

        if asserted_at:
            now_dt = current_time or datetime.now(timezone.utc)
            if asserted_at.tzinfo is None:
                asserted_at = asserted_at.replace(tzinfo=timezone.utc)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)

            days_old = max(0.0, (now_dt - asserted_at).total_seconds() / 86400.0)
            decay_factor = math.pow(0.5, days_old / DEFAULT_HALF_LIFE_DAYS)
            raw_score = raw_score * decay_factor

    return round(max(0.0, min(1.0, raw_score)), 4)
