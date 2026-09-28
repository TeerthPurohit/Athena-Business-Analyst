"""Evidence-based technical reference that keeps design detail out of the BRD."""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import label, plain, requirements
from agents.business_analyst.capabilities.projection.document_template import (
    contents,
    front_matter,
    project_document_context,
    section,
)
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Technical Reference and Decision Notes"
NEEDS = "technical context or implementation details"

_TECHNICAL_TERMS = re.compile(
    r"\b(JSONL|SQLite|tree-sitter|LSP|MCP|OAuth|PKCE|Python|Rust|Docker|keyring|"
    r"provider adapter|CLI command|database schema|API endpoint|container image|"
    r"REST|PostgreSQL|Redis|WebSocket|OpenAPI|deployment)\b",
    re.IGNORECASE,
)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "; ".join(text for item in value if (text := _text(item)))
    if isinstance(value, dict):
        return "; ".join(
            f"{label(key)}: {text}" for key, item in value.items() if (text := _text(item))
        )
    return plain(value)


def _items(value):
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return list(dict.fromkeys(text for item in values if (text := _text(item))))


def _recorded_section(context, *keys):
    text = section(context, *keys)
    return "" if not text or text == "Not yet specified in the project record." else text


def _decision_candidates(reqs):
    candidates = []
    for _, requirement_id, value in reqs:
        kind = _text(value.get("requirement_type") or value.get("category")).lower().replace("-", "_")
        statement = " ".join(
            _text(value.get(field)) for field in ("task", "object", "data_format", "comments")
        ).strip()
        if kind == "architecture_decision":
            classification = "Explicit architecture decision"
        elif _TECHNICAL_TERMS.search(statement):
            classification = "Potential technical detail - confirm classification"
        else:
            continue
        candidates.append((requirement_id, classification, statement, value))
    return candidates


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    reqs = requirements(facts)
    name, summary, context, control = await project_document_context(ctx, session)
    candidates = _decision_candidates(reqs)

    sections = (
        ("System Context", "2 System Context", ("systems_technology", "platforms", "products_services")),
        ("Data and Persistence", "3 Data and Persistence", ("data", "data_management", "databases", "storage")),
        ("Interfaces and Integrations", "4 Interfaces and Integrations", ("integrations", "interfaces", "apis")),
        ("Deployment and Operations", "5 Deployment and Operations", ("deployment", "infrastructure", "operations", "monitoring")),
        ("Security and Identity", "6 Security and Identity", ("security", "authentication", "compliance")),
    )
    available = []
    for context_title, document_title, keys in sections:
        context_detail = _recorded_section(context, *keys)
        summary_items = []
        for key in keys:
            summary_items.extend(_items(summary.get(key)))
        summary_items = list(dict.fromkeys(summary_items))
        if context_detail or summary_items:
            available.append((context_title, document_title, context_detail, summary_items))
    if not candidates and not available:
        return None

    metadata, references = await front_matter(
        TITLE, name, context, control, facts, ctx, session, max_sources=20
    )
    available_by_title = {
        document_title: (context_detail, summary_items)
        for _, document_title, context_detail, summary_items in available
    }
    selected_sections = [
        (document_title, *available_by_title[document_title])
        for _, document_title, _ in sections
        if document_title in available_by_title
    ]
    decision_number = len(selected_sections) + 2
    review_number = decision_number + 1
    decision_heading = f"{decision_number} Architecture Decision Candidates"
    review_heading = f"{review_number} Decisions Requiring Technical Review"
    section_titles = (
        ["1 Purpose and Use"]
        + [f"{index + 2} {title}" for index, (title, _, _) in enumerate(selected_sections)]
        + [decision_heading, review_heading]
    )
    md = metadata + "## Source References\n" + references + "\n"
    md += contents(section_titles)
    md += (
        "## 1 Purpose and Use\n\n"
        "This technical reference separates implementation detail from the business case. It records only technical "
        "context present in the project evidence. Entries are drafts; a technical decision is not approved until its "
        "owner, rationale, alternatives, and consequences are reviewed and recorded.\n\n"
    )
    for index, (document_title, context_detail, summary_items) in enumerate(selected_sections, 2):
        md += f"## {index} {document_title}\n\n"
        if context_detail:
            md += context_detail.strip() + "\n\n"
        if summary_items:
            md += "Recorded project summary:\n"
            md += "".join(f"- {item}\n" for item in summary_items) + "\n"

    md += f"## {decision_heading}\n\n"
    if not candidates:
        md += "No architecture decision or implementation-specific requirement was identified in the current evidence.\n\n"
    else:
        md += (
            f"{len(candidates)} source requirements are explicit decisions or potential technical-design details. "
            "Potential items are labeled for review; they have not been promoted to approved decisions.\n\n"
        )
        for index, (requirement_id, classification, statement, value) in enumerate(candidates, 1):
            md += f"### TD-{index:03d} - {classification}\n\n"
            md += f"**Recorded detail:** {statement or 'No decision statement is recorded.'}\n\n"
            for heading, field in (
                ("Context", "business_problem"),
                ("Business objective", "business_objective"),
                ("Constraints", "constraints"),
                ("Dependencies", "dependencies"),
                ("Owner", "owner"),
                ("Consequences recorded in evidence", "outcomes"),
            ):
                detail = _text(value.get(field))
                if detail:
                    md += f"**{heading}:** {detail}\n\n"
            md += f"**Traceability:** {requirement_id}\n\n"
    md += f"## {review_heading}\n\n"
    missing_topics = [context_title for context_title, document_title, _ in sections if document_title not in available_by_title]
    if missing_topics:
        md += "**Technical topics not yet documented:** " + ", ".join(missing_topics) + ".\n\n"
    md += (
        "Before approving a technical decision, record the decision owner, technical context, alternatives considered, "
        "selection rationale, consequences, and review date in an ADR. Database schemas, API contracts, and deployment "
        "specifications should be maintained as separate technical specifications when the project evidence is ready.\n"
    )
    return md
