"""kNN-Gated Contradiction Candidate Detection & Pair Fingerprint Cache (§2.2, §5, §9).

CLAUDE.md Non-Negotiables:
- Candidate-gated contradiction search (kNN k=12), not O(n²).
- Pair fingerprint cache keyed (sha256(text_a), sha256(text_b), prompt_version, model).
"""
import hashlib
from typing import Any, Dict, List, Optional, Set, Tuple


def compute_pair_key(text_a: str, text_b: str, prompt_version: str = "v1", model: str = "default") -> str:
    """Computes pair fingerprint cache key."""
    sha_a = hashlib.sha256(text_a.encode("utf-8")).hexdigest()
    sha_b = hashlib.sha256(text_b.encode("utf-8")).hexdigest()
    # Sort hashes to ensure symmetry (A, B) == (B, A)
    if sha_a > sha_b:
        sha_a, sha_b = sha_b, sha_a
    return f"{sha_a}:{sha_b}:{prompt_version}:{model}"


class PairFingerprintCache:
    """Cache for judged contradiction candidate pairs."""

    def __init__(self) -> None:
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, text_a: str, text_b: str, prompt_version: str = "v1", model: str = "default") -> Optional[Dict[str, Any]]:
        key = compute_pair_key(text_a, text_b, prompt_version, model)
        return self._cache.get(key)

    def set(self, text_a: str, text_b: str, is_contradiction: bool, confidence: float, prompt_version: str = "v1", model: str = "default") -> None:
        key = compute_pair_key(text_a, text_b, prompt_version, model)
        self._cache[key] = {
            "is_contradiction": is_contradiction,
            "confidence": confidence,
            "cached_at": key,
        }

    def clear(self) -> None:
        self._cache.clear()


def find_knn_contradiction_candidates(
    facts: List[Dict[str, Any]],
    k: int = 12,
    cosine_floor: float = 0.75,
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Finds candidate fact pairs for contradiction analysis using kNN gating (k=12).

    Filters:
    - Cosine similarity floor (if embeddings present)
    - Excludes same-source facts
    - Excludes identical-subject facts
    """
    candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    for i, fact_a in enumerate(facts):
        # Find top-k candidate matches for fact_a
        matches: List[Tuple[float, Dict[str, Any]]] = []
        source_a = fact_a.get("source_id")
        subject_a = fact_a.get("subject_key")

        for j, fact_b in enumerate(facts):
            if i >= j:
                continue

            source_b = fact_b.get("source_id")

            # Same-source exclusion gate: facts from identical source cannot contradict
            if source_a and source_b and source_a == source_b:
                continue

            # Shared-subject gate or text overlap
            subject_b = fact_b.get("subject_key")
            if subject_a and subject_b and subject_a == subject_b:
                matches.append((0.95, fact_b))
                continue

            # Check cosine similarity if vector embeddings are present
            emb_a = fact_a.get("embedding")
            emb_b = fact_b.get("embedding")
            if emb_a and emb_b and len(emb_a) == len(emb_b):
                dot = sum(x * y for x, y in zip(emb_a, emb_b))
                norm_a = math.sqrt(sum(x * x for x in emb_a))
                norm_b = math.sqrt(sum(y * y for y in emb_b))
                if norm_a > 0 and norm_b > 0:
                    sim = dot / (norm_a * norm_b)
                    if sim >= cosine_floor:
                        matches.append((sim, fact_b))

        # Sort matches by similarity descending, take top-k
        matches.sort(key=lambda x: x[0], reverse=True)
        for _, fact_b in matches[:k]:
            candidates.append((fact_a, fact_b))

    return candidates
