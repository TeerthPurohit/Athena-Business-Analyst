"""Acquisition capabilities package (§2.1, §11.6).

Acquisition capabilities collect external knowledge into raw facts.
"""
from typing import Any, Dict, List, Optional
import uuid


def run_document_analysis(source_id: str, document_text: str) -> List[Dict[str, Any]]:
    """Parses raw text document into structured attribute facts."""
    facts = []
    lines = [l.strip() for l in document_text.splitlines() if l.strip()]
    for idx, line in enumerate(lines[:10]):
        facts.append({
            "id": str(uuid.uuid4()),
            "subject_type": "DocumentSection",
            "subject_key": f"section_{idx+1}",
            "predicate": "content",
            "value": {"text": line},
            "source_id": source_id,
        })
    return facts


def run_interview(source_id: str, transcript_text: str, stakeholder_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Extracts quotes and facts from a stakeholder interview transcript."""
    return [
        {
            "id": str(uuid.uuid4()),
            "subject_type": "InterviewQuote",
            "subject_key": f"quote_{stakeholder_id or 'anon'}_1",
            "predicate": "statement",
            "value": {"text": transcript_text[:200], "stakeholder_id": stakeholder_id},
            "source_id": source_id,
        }
    ]


def run_compliance_lookup(source_id: str, domain: str) -> List[Dict[str, Any]]:
    """Extracts compliance & regulatory constraint facts."""
    return [
        {
            "id": str(uuid.uuid4()),
            "subject_type": "Constraint",
            "subject_key": f"compliance_{domain}_1",
            "predicate": "regulatory_requirement",
            "value": {"domain": domain, "text": f"Compliance mandate for {domain}"},
            "source_id": source_id,
        }
    ]


def run_market_research(source_id: str, topic: str) -> List[Dict[str, Any]]:
    """Extracts market research & competitor benchmark facts."""
    return [
        {
            "id": str(uuid.uuid4()),
            "subject_type": "MarketFact",
            "subject_key": f"market_{topic}_1",
            "predicate": "benchmark",
            "value": {"topic": topic, "text": f"Market benchmark data for {topic}"},
            "source_id": source_id,
        }
    ]
