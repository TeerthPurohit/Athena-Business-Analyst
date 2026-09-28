"""Business-first BRD renderer with traceable, consolidated business requirements."""

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import (
    REQUIREMENTS_NEEDED,
    context_records,
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
    table,
)
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Business Requirements Document (BRD)"
NEEDS = REQUIREMENTS_NEEDED


def _items(value):
    if isinstance(value, (list, tuple)):
        return list(dict.fromkeys(text for text in map(plain, value) if text))
    text = plain(value)
    if not text:
        return []
    if "\n" in text:
        return list(dict.fromkeys(line.strip(" -*\t") for line in text.splitlines() if line.strip(" -*\t")))
    return [text]


def _summary_items(summary, *keys):
    result = []
    for key in keys:
        result.extend(_items(summary.get(key)))
    return list(dict.fromkeys(result))


def _bullets(values, empty_note):
    return "".join(f"- {value}\n" for value in values) if values else f"{empty_note}\n"


def _stakeholder_rows(summary, context):
    rows = []
    seen = set()
    records = context_records(context, "stakeholders") + context_records(context, "users_personas")
    for record in records:
        name = plain(record.get("name") or record.get("role") or record.get("persona") or record.get("title"))
        role = plain(record.get("role") or record.get("type"))
        interest = plain(record.get("goal") or record.get("needs") or record.get("interest") or record.get("pain"))
        authority = plain(record.get("decision_authority") or record.get("authority"))
        key = (name, role, interest, authority)
        if key in seen:
            continue
        seen.add(key)
        rows.append((name or role or "Stakeholder", role, interest, authority))
    for stakeholder in _items(summary.get("stakeholders")):
        key = (stakeholder, "", "", "")
        if key not in seen:
            seen.add(key)
            rows.append((stakeholder, "", "", ""))
    return rows


def _business_requirement_markdown(items):
    if not items:
        return (
            "No business-level requirements could be consolidated from the current evidence. "
            "Add or confirm the problem, affected stakeholders, and desired business outcomes. "
            "The detailed source requirements remain available in the Requirements Register.\n"
        ), 0

    blocks = []
    needs_clarification = 0
    for item in items:
        fields = [
            ("Business problem", item.get("business_problem")),
            ("Business objective", item.get("business_objective")),
            ("Stakeholder", item.get("stakeholder")),
            ("Business benefit", item.get("business_benefit")),
            ("Priority", item.get("priority")),
            ("Success measure", item.get("success_metric")),
        ]
        missing = [name for name, value in fields if not plain(value)]
        questions = _items(item.get("clarification_questions"))
        if missing or questions:
            needs_clarification += 1

        block = [f"### {item['id']} — {plain(item.get('title'))}\n", f"{plain(item.get('statement'))}\n"]
        for name, value in fields:
            if plain(value):
                block.append(f"- **{name}:** {plain(value)}\n")
        source_ids = _items(item.get("source_requirement_ids"))
        if source_ids:
            block.append(f"- **Detailed requirements:** {', '.join(source_ids)}\n")
        if questions:
            block.append("- **Needs stakeholder decision:** " + " ".join(questions) + "\n")
        elif missing:
            block.append("- **Draft review:** Confirm " + ", ".join(missing) + ".\n")
        blocks.append("\n".join(block) + "\n")
    return "".join(blocks), needs_clarification


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    reqs = requirements(facts)
    if not reqs:
        return None

    name, summary, context, control = await project_document_context(ctx, session)
    analysis = await get_business_analysis(ctx, session, name, summary, context, reqs)
    business_requirements = numbered_business_requirements(analysis)
    br_markdown, clarification_count = _business_requirement_markdown(business_requirements)
    metadata, references = await front_matter(
        TITLE, name, context, control, facts, ctx, session, max_sources=20
    )

    problem = _summary_items(summary, "problem_statement", "project_purpose")
    objectives = _summary_items(summary, "business_goals", "vision", "mission")
    scope = _summary_items(summary, "functional_scope", "in_scope_features", "must_have_features", "should_have_features")
    out_of_scope = _summary_items(summary, "out_of_scope_features", "wont_have_features")
    business_rules = _summary_items(summary, "key_business_rules")
    success_measures = _summary_items(summary, "success_measures")
    assumptions = _summary_items(summary, "important_assumptions")
    constraints = _summary_items(summary, "constraints")
    risks = _summary_items(summary, "risks")
    dependencies = _summary_items(summary, "dependencies")
    stakeholders = _stakeholder_rows(summary, context)
    process_flows = section(context, "processes", "workflows")
    glossary = section(context, "glossary")

    section_titles = [
        "1 Executive Summary", "2 Problem Statement", "3 Business Objectives",
        "4 Current State and Pain Points", "5 Stakeholders and Personas", "6 Scope",
        "7 Out of Scope", "8 Business Requirements", "9 Business Rules",
        "10 Success Metrics and KPIs", "11 Assumptions and Constraints",
        "12 Risks and Dependencies", "13 High-Level Process Flows", "14 Glossary",
        "15 Requirement Quality Review", "16 Detailed Requirement Traceability",
    ]
    md = metadata + "## Source References\n" + references + "\n"
    md += contents(section_titles)
    executive_summary = plain(analysis.executive_summary) or \
        "The current project record does not yet support a complete business summary. Review the problem, objectives, and stakeholder needs before approving this draft."
    md += f"## 1 Executive Summary\n\n{executive_summary}\n\n"
    md += "## 2 Problem Statement\n\n" + _bullets(problem, "The business problem needs stakeholder confirmation.") + "\n"
    md += "## 3 Business Objectives\n\n" + _bullets(objectives, "Business objectives have not been confirmed.") + "\n"
    md += "## 4 Current State and Pain Points\n\n"
    md += _bullets(problem, "Current-state pain points have not been recorded.")
    current_state = _items(summary.get("other_context"))
    if current_state:
        md += _bullets(current_state, "")
    md += "\n## 5 Stakeholders and Personas\n\n"
    md += table(["Stakeholder / Persona", "Role", "Need or Interest", "Decision Authority"],
                stakeholders or [("", "", "", "")], missing="")
    if not stakeholders:
        md += "\nStakeholder ownership needs confirmation before approval.\n"
    md += "\n## 6 Scope\n\n" + _bullets(scope, "In-scope outcomes have not been confirmed.") + "\n"
    md += "## 7 Out of Scope\n\n" + _bullets(out_of_scope, "No explicit exclusions are recorded yet.") + "\n"
    md += "## 8 Business Requirements\n\n" + br_markdown + "\n"
    md += "## 9 Business Rules\n\n"
    context_rules = section(context, "business_rules")
    recorded_context_rules = bool(
        context_rules and context_rules != "Not yet specified in the project record."
    )
    if business_rules:
        md += _bullets(business_rules, "")
    if recorded_context_rules:
        md += context_rules + "\n"
    if not business_rules and not recorded_context_rules:
        md += "No business rules have been confirmed.\n"
    md += "\n## 10 Success Metrics and KPIs\n\n"
    md += _bullets(success_measures, "No success metric or target has been agreed. Stakeholders should define how success will be measured; this draft does not invent targets.")
    md += "\n## 11 Assumptions and Constraints\n\n### Assumptions\n"
    md += _bullets(assumptions, "No assumptions have been confirmed.")
    md += "\n### Constraints\n" + _bullets(constraints, "No business or delivery constraints have been confirmed.") + "\n"
    md += "## 12 Risks and Dependencies\n\n### Risks\n" + _bullets(risks, "No project risks are recorded.")
    md += "\n### Dependencies\n" + _bullets(dependencies, "No dependencies are recorded.") + "\n"
    md += "## 13 High-Level Process Flows\n\n"
    md += process_flows if process_flows and process_flows != "Not yet specified in the project record." else "No reviewed high-level process flow is recorded yet.\n"
    md += "\n## 14 Glossary\n\n"
    md += glossary if glossary and glossary != "Not yet specified in the project record." else "No project-specific terms are recorded.\n"
    md += "\n## 15 Requirement Quality Review\n\n"
    if business_requirements:
        md += f"{clarification_count} of {len(business_requirements)} business requirement groups need stakeholder decisions or missing-field review. All groups remain drafts until reviewed and approved.\n"
    else:
        md += "Business requirement consolidation is incomplete; no group is represented as approved.\n"
    md += "\nReview priority, business owner, benefit, measurable outcome where applicable, and evidence for each business requirement. Detailed field-level gaps are listed in the Requirements Register.\n"
    md += "\n## 16 Detailed Requirement Traceability\n\n"
    md += "Each business requirement lists its source REQ identifiers. The Requirements Register workbook contains every detailed requirement, evidence reference, quality status, and the mapping back to its business requirement. Technical design choices are kept in the register's Technical Decisions sheet.\n"
    return md
