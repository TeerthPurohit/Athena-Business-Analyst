"""Tests for gap_ranking.py (Feature 3 — pure, deterministic, zero LLM calls)."""
from unittest.mock import MagicMock, patch
import pytest

from agents.business_analyst.quality.gap_ranking import (
    coverage_gain,
    detect_value_conflict,
    rank_gaps,
)
from agents.business_analyst.quality.ambiguity import detect_ambiguities


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

NODES = [
    {"id": "req:login", "type": "Requirement", "attrs": {"description": None, "priority": "high"}},
    {"id": "nfr:perf", "type": "NFR", "attrs": {"threshold": "<UNSPECIFIED: threshold>"}},
    {"id": "proc:checkout", "type": "ProcessStep", "attrs": {"actor": "User", "steps": None}},
]

EDGES = [
    {"source_id": "req:login", "target_id": "nfr:perf"},
    {"source_id": "req:login", "target_id": "proc:checkout"},
]


AMBIGUITIES = [
    {"node_id": "req:login", "node_type": "Requirement", "field": "description",
     "placeholder": "description", "kind": "missing_value"},
    {"node_id": "nfr:perf", "node_type": "NFR", "field": "threshold",
     "placeholder": "threshold", "kind": "unspecified_placeholder"},
    {"node_id": "proc:checkout", "node_type": "ProcessStep", "field": "steps",
     "placeholder": "steps", "kind": "missing_value"},
]


# ---------------------------------------------------------------------------
# coverage_gain
# ---------------------------------------------------------------------------

def test_coverage_gain_returns_float():
    """coverage_gain returns a non-negative float."""
    gap = AMBIGUITIES[0]
    score = coverage_gain(gap, NODES, EDGES)
    assert isinstance(score, float)
    assert score >= 0.0


def test_coverage_gain_higher_criticality_node_scores_higher():
    """Requirement gap scores higher than ProcessStep gap (higher criticality)."""
    req_gap = AMBIGUITIES[0]  # Requirement, criticality 1.0
    proc_gap = AMBIGUITIES[2]  # ProcessStep, criticality 0.75
    req_score = coverage_gain(req_gap, NODES, EDGES)
    proc_score = coverage_gain(proc_gap, NODES, EDGES)
    assert req_score >= proc_score


def test_coverage_gain_node_with_outgoing_edges_scores_higher():
    """req:login has 2 outgoing edges; nfr:perf has 0 — req should score higher."""
    req_gap = AMBIGUITIES[0]   # req:login, fan_out=2
    nfr_gap = AMBIGUITIES[1]   # nfr:perf, fan_out=0
    req_score = coverage_gain(req_gap, NODES, EDGES)
    nfr_score = coverage_gain(nfr_gap, NODES, EDGES)
    assert req_score >= nfr_score


# ---------------------------------------------------------------------------
# rank_gaps
# ---------------------------------------------------------------------------

def test_rank_gaps_returns_sorted_descending():
    """rank_gaps returns gaps sorted highest score first."""
    ranked = rank_gaps(AMBIGUITIES, NODES, EDGES)
    scores = [g["_score"] for g in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_gaps_returns_all_gaps():
    """rank_gaps returns the same number of gaps as the input."""
    ranked = rank_gaps(AMBIGUITIES, NODES, EDGES)
    assert len(ranked) == len(AMBIGUITIES)


def test_rank_gaps_empty_input_returns_empty():
    """rank_gaps handles an empty ambiguities list."""
    assert rank_gaps([], NODES, EDGES) == []


def test_rank_gaps_score_field_added():
    """Each ranked gap has a _score field."""
    ranked = rank_gaps(AMBIGUITIES, NODES, EDGES)
    for gap in ranked:
        assert "_score" in gap


# ---------------------------------------------------------------------------
# ZERO LLM CALLS ASSERTION (mandatory per CLAUDE.md)
# ---------------------------------------------------------------------------

def test_rank_gaps_makes_zero_llm_calls():
    """rank_gaps is pure — it must NEVER invoke any LLM client."""
    with patch("agents.business_analyst.quality.gap_ranking.find_knn_contradiction_candidates") as mock_knn, \
         patch("agents.business_analyst.llm_client.get_structured_output") as mock_llm:
        rank_gaps(AMBIGUITIES, NODES, EDGES)
        mock_llm.assert_not_called()


def test_coverage_gain_makes_zero_llm_calls():
    """coverage_gain is pure — it must NEVER invoke any LLM client."""
    with patch("agents.business_analyst.llm_client.get_structured_output") as mock_llm:
        coverage_gain(AMBIGUITIES[0], NODES, EDGES)
        mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# detect_value_conflict
# ---------------------------------------------------------------------------

def test_detect_value_conflict_finds_conflict():
    """Returns a conflict dict when new answer differs from a prior answer."""
    prior_facts = [{"subject_key": "abc123", "value": {"answer": "Yes, always"}}]
    conflict = detect_value_conflict("No, never", prior_facts)
    assert conflict is not None
    assert conflict["prior"] == "Yes, always"
    assert conflict["new"] == "No, never"


def test_detect_value_conflict_no_conflict_when_same():
    """Returns None when new answer matches prior answer (case-insensitive, stripped)."""
    prior_facts = [{"subject_key": "abc123", "value": {"answer": "Yes"}}]
    conflict = detect_value_conflict("yes", prior_facts)
    assert conflict is None


def test_detect_value_conflict_no_prior_facts_returns_none():
    """Returns None when prior_answer_facts is empty."""
    assert detect_value_conflict("any answer", []) is None


def test_detect_value_conflict_empty_new_answer_returns_none():
    """Returns None when new_answer is empty string."""
    prior = [{"subject_key": "abc", "value": {"answer": "Prior"}}]
    assert detect_value_conflict("", prior) is None


# ---------------------------------------------------------------------------
# detect_ambiguities integration (feeds into rank_gaps)
# ---------------------------------------------------------------------------

def test_detect_ambiguities_feeds_rank_gaps():
    """detect_ambiguities output can be passed directly to rank_gaps without transformation."""
    raw_ambiguities = detect_ambiguities(NODES)
    assert len(raw_ambiguities) > 0
    ranked = rank_gaps(raw_ambiguities, NODES, EDGES)
    assert len(ranked) == len(raw_ambiguities)
