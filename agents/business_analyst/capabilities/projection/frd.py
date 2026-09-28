"""Compact, test-oriented Functional Requirements Document renderer."""

from collections import defaultdict
import re

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import (
    REQUIREMENTS_NEEDED,
    label,
    plain,
    requirements,
)
from agents.business_analyst.capabilities.projection.business_analysis import (
    get_business_analysis,
    numbered_business_requirements,
)
from agents.business_analyst.capabilities.projection.document_template import (
    contents,
    front_matter,
    project_document_context,
    section,
)
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Functional Requirements Document (FRD)"
NEEDS = REQUIREMENTS_NEEDED

_PREFIXES = {
    "functional": "FR",
    "nonfunctional": "NFR",
    "business_rule": "BRULE",
    "constraint": "CST",
    "assumption": "ASM",
    "transitional": "TR",
}


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


def _type(value):
    raw = str(value.get("requirement_type") or "").strip().lower().replace("-", "_")
    if raw in {"business", "stakeholder", "functional", "nonfunctional", "business_rule", "constraint", "assumption", "architecture_decision", "transitional"}:
        return raw
    category = str(value.get("category") or "functional").lower().replace("-", "_")
    return category if category in {"business", "stakeholder", "functional", "nonfunctional", "transitional"} else "functional"


def _area(value):
    explicit = _text(value.get("functional_area"))
    if explicit:
        return explicit
    haystack = " ".join(
        _text(value.get(key)) for key in ("task", "object", "business_rules", "constraints")
    ).lower()
    groups = (
        ("Authentication and Authorization", ("auth", "oauth", "login", "credential", "sign in", "permission", "role")),
        ("Session Management", ("session", "checkpoint", "resume", "conversation state")),
        ("Agent Execution", ("agent", "tool call", "execution loop", "backpressure", "run task")),
        ("Verification", ("verify", "verification", "test", "acceptance criteria", "quality gate")),
        ("Repository and Code Graph", ("repository", "code graph", "tree-sitter", "lsp", "index code")),
        ("Provider and Model Management", ("provider", "model", "backend", "routing", "reasoning effort")),
        ("Security and Privacy", ("security", "secret", "sandbox", "encryption", "privacy")),
        ("Voice and Conversation", ("voice", "speech", "transcri", "audio", "microphone")),
        ("Deployment and Operations", ("deploy", "docker", "container", "monitor", "logging", "observability")),
        ("Data and Persistence", ("database", "sqlite", "jsonl", "persist", "storage", "state file")),
    )
    for area, needles in groups:
        if any(needle in haystack for needle in needles):
            return area
    obj = _text(value.get("object"))
    return obj[:80].strip().capitalize() if obj else "General System Requirements"


def _statement(value):
    task = _text(value.get("task"))
    obj = _text(value.get("object"))
    if not task:
        return "Requirement intent is not clear enough to state; see the Requirements Register for the source record and missing information."
    if task.lower().startswith(("as a ", "as an ")):
        statement = task
    elif re.match(r"^(the )?(system|application|service|platform)\s+(shall|must|should)\b", task, re.I):
        statement = task
    elif task.lower().startswith(("shall ", "must ", "should ")):
        statement = "The system " + task
    else:
        statement = "The system shall " + task[:1].lower() + task[1:]
    if obj and obj.casefold() not in statement.casefold():
        statement = statement.rstrip(" .") + f" — {obj}"
    statement = statement.strip()
    if statement and statement[-1] not in ".?!":
        statement += "."
    return statement


def _acceptance(value):
    explicit = _items(value.get("acceptance_criteria"))
    if explicit:
        return "<br>".join(f"• {item}" for item in explicit)
    clauses = []
    given = _items(value.get("preconditions"))
    when = _items(value.get("trigger"))
    then = _items(value.get("outcomes"))
    if given:
        clauses.append("**Given** " + "; ".join(given))
    if when:
        clauses.append("**When** " + "; ".join(when))
    if then:
        clauses.append("**Then** " + "; ".join(then))
    return "<br>".join(clauses) or "Needs stakeholder clarification; no acceptance behavior is stated in the evidence."


def _known_details(value):
    fields = (
        ("Rules", "business_rules"),
        ("Dependencies", "dependencies"),
        ("Assumptions", "assumptions"),
        ("Constraints", "constraints"),
        ("Inputs", "input_data"),
        ("Outputs", "output_data"),
        ("Format", "data_format"),
        ("Exceptions", "exceptions"),
    )
    details = []
    for heading, key in fields:
        values = _items(value.get(key))
        if values:
            details.append(f"**{heading}:** " + "; ".join(values))
    return "<br>".join(details)


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    reqs = requirements(facts)
    if not reqs:
        return None

    name, summary, context, control = await project_document_context(ctx, session)
    analysis = await get_business_analysis(ctx, session, name, summary, context, reqs)
    business_requirements = numbered_business_requirements(analysis)
    business_ids_by_requirement = defaultdict(list)
    for business_requirement in business_requirements:
        for source_id in business_requirement.get("source_requirement_ids", []):
            business_ids_by_requirement[source_id].append(business_requirement["id"])

    groups = defaultdict(list)
    counts = defaultdict(int)
    criteria_missing = 0
    for _, requirement_id, value in reqs:
        requirement_type = _type(value)
        if requirement_type in {"business", "stakeholder", "architecture_decision"}:
            continue
        prefix = _PREFIXES.get(requirement_type, "FR")
        counts[requirement_type] += 1
        functional_id = f"{prefix}-{counts[requirement_type]:03d}"
        statement = _statement(value)
        criteria = _acceptance(value)
        if "Needs stakeholder clarification" in criteria:
            criteria_missing += 1
        area = _area(value)
        source_br_ids = business_ids_by_requirement.get(requirement_id, [])
        source_trace = f"**Source:** {requirement_id}"
        if source_br_ids:
            source_trace += " · **Business requirement:** " + ", ".join(source_br_ids)
        stakeholder = _text(value.get("stakeholder"))
        if stakeholder:
            source_trace += f" · **Stakeholder:** {stakeholder}"
        groups[area].append((functional_id, requirement_id, value, statement, criteria, _known_details(value), source_trace))

    section_titles = [
        "1 Purpose and Audience",
        "2 Business Context and System Boundary",
        "3 Requirements Overview",
        "4 Functional Requirements by Area",
        "5 Cross-Cutting Requirements",
        "6 Exceptions, Dependencies, and Assumptions",
        "7 Acceptance and Quality Review",
        "8 Traceability and Technical Decisions",
    ]
    metadata, references = await front_matter(
        TITLE, name, context, control, facts, ctx, session, frd=True, max_sources=20
    )
    md = metadata + "## Source References\n" + references + "\n"
    md += contents(section_titles)
    md += "## 1 Purpose and Audience\n\n"
    md += "This draft specifies evidenced system behavior for engineering and QA review. Detailed requirement records and source evidence are in the Requirements Register workbook. Missing decisions remain draft items and are not represented as approved.\n\n"
    md += "## 2 Business Context and System Boundary\n\n"
    process_context = section(context, "processes", "workflows")
    if process_context and process_context != "Not yet specified in the project record.":
        md += process_context + "\n\n"
    md += "\nBusiness outcomes, scope, stakeholders, and success measures are summarized in the BRD. The functional specification preserves implementation detail only where it is needed to describe an evidenced system behavior.\n\n"
    md += "## 3 Requirements Overview\n\n"
    md += f"The project record contains {len(reqs)} detailed requirement records. This FRD includes {sum(counts.values())} functional, nonfunctional, business-rule, constraint, assumption, and transitional records across {len(groups)} areas. Explicit architecture decisions are listed in the Requirements Register’s Technical Decisions sheet.\n\n"
    md += "| Requirement type | Count |\n| --- | ---: |\n"
    for requirement_type in ("functional", "nonfunctional", "business_rule", "constraint", "assumption", "transitional"):
        if counts[requirement_type]:
            md += f"| {label(requirement_type)} | {counts[requirement_type]} |\n"
    md += "\nAll identifiers link back to the Requirements Register’s source REQ identifiers.\n\n"

    md += "## 4 Functional Requirements by Area\n\n"
    md += "Each item shows its known requirement statement, acceptance behavior, and only the additional conditions present in the project evidence. Blank template fields are omitted.\n\n"
    if not groups:
        md += "No functional or supporting system requirements are ready for this section. Review the classification and missing-field details in the Requirements Register.\n\n"
    for area, entries in groups.items():
        md += f"### {area} ({len(entries)} requirements)\n\n"
        for functional_id, requirement_id, value, statement, criteria, details, source_trace in entries:
            title = _text(value.get("object") or value.get("task"))
            title = re.sub(r"\s+", " ", title).strip(" .")[:120] or "Requirement intent needs clarification"
            md += f"**{functional_id} — {title}**\n\n"
            md += f"{statement}\n\n"
            md += f"**Acceptance:** {criteria}\n\n"
            if details:
                md += f"{details}\n\n"
            md += f"{source_trace}\n\n"

    md += "## 5 Cross-Cutting Requirements\n\n"
    md += "Nonfunctional requirements, business rules, constraints, and assumptions are included in the area sections above and identified by their NFR/BRULE/CST/ASM prefixes. Their complete metadata remains available in the register.\n\n"
    md += "## 6 Exceptions, Dependencies, and Assumptions\n\n"
    md += "Per-requirement exceptions, dependencies, and assumptions appear only when the source evidence records them. The Requirements Register also lists any missing decisions; no values have been inferred to fill those gaps.\n\n"
    md += "## 7 Acceptance and Quality Review\n\n"
    md += f"{criteria_missing} included requirements have no explicit acceptance criteria or enough evidenced trigger/outcome information to derive them. They remain draft and need stakeholder clarification before approval.\n\n"
    md += "Requirements with incomplete ownership, priority, business linkage, dependencies, constraints, or source traceability are flagged in the register’s Quality Status and Missing Information columns.\n\n"
    md += "## 8 Traceability and Technical Decisions\n\n"
    md += "Requirement IDs in this FRD link to source REQ identifiers. Where the project record establishes a business requirement grouping, the corresponding BR identifier is shown. Architecture decisions and implementation-specific source details are kept in the Requirements Register workbook’s Technical Decisions sheet for design review and ADR follow-up.\n"
    return md
