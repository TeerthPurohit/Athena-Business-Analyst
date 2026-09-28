"""Reference-aligned FRD renderer, populated only from the project record."""
from sqlalchemy.ext.asyncio import AsyncSession
from agents.business_analyst.capabilities.projection import (
    REQUIREMENTS_NEEDED, constraint_text, filled, label, requirements,
)
from agents.business_analyst.capabilities.projection.document_template import (
    MISSING, contents, data_dictionary, fields, front_matter, project_document_context, section, summary_value, table,
)
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Functional Requirements Document (FRD)"
NEEDS = REQUIREMENTS_NEEDED


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    reqs = requirements(facts)
    if not reqs:
        return None
    name, summary, context, control = await project_document_context(ctx, session)
    front, references = await front_matter(TITLE, name, context, control, facts, ctx, session, frd=True)
    body = "## 1.0 Introduction\n\n### 1.1 Purpose\nThis document describes the functional requirements for " + name + ", supporting implementation and stakeholder review.\n\n"
    body += "### 1.2 Document Conventions\nRequirement identifiers match the BRD and traceability register. Unspecified fields require a stakeholder decision; they are not inferred.\n\n"
    body += "### 1.3 User Problem / Project Background\n" + summary_value(summary, "problem_statement", "project_purpose") + "\n\n"
    body += "### 1.4 Solution / Solution Scope\n\n#### 1.4.1 Included in Scope\n" + summary_value(summary, "functional_scope", "in_scope_features", "must_have_features", "should_have_features") + "\n\n"
    body += "#### 1.4.2 Excluded in Scope\n" + summary_value(summary, "out_of_scope_features", "wont_have_features") + "\n\n"
    body += "## 2.0 References\n" + references + "\n"
    body += "## 3.0 Methodology / Solution Approach\n" + section(context, "projects", "timeline") + "\n\n"
    body += "## 4.0 Solution Overall Description\n\n### 4.1 Solution Perspective\n" + section(context, "systems_technology", "products_services") + "\n\n"
    body += "### 4.2 Solution Features\n" + table(["Requirement", "Feature", "Object"], [(name_, val.get("task"), val.get("object")) for _, name_, val in reqs]) + "\n"
    body += "### 4.3 User Classes and Characteristics\n" + section(context, "users_personas", "stakeholders") + "\n\n"
    body += "### 4.4 Operating Environment\n" + section(context, "systems_technology") + "\n\n"
    body += "### 4.5 Design and Implementation Constraints\n" + section(context, "constraints") + "\n\n"
    body += "### 4.6 User Documentation / User Manual\n" + section(context, "sops", "documents") + "\n\n"
    body += "## 5.0 Solution Requirements\n\n### 5.1 Functional Requirements\n"
    for n, (_, req_name, val) in enumerate(reqs, 1):
        constraints = [text for text in map(constraint_text, val.get("constraints") or []) if text]
        body += f"\n#### 5.1.{n} {req_name}\n" + table(["Item", "Description"], [
            ("Requirement #", req_name), ("Requirement Description", val.get("task")),
            ("Category", label(val.get("category") or "uncategorized")),
            ("Stakeholder", filled(val, "stakeholder")), ("Object", filled(val, "object")),
            ("Trigger", filled(val, "trigger")), ("Preconditions", filled(val, "preconditions")),
            ("Dependency", val.get("dependencies")), ("Success", val.get("outcomes")),
            ("Expected outcomes", filled(val, "outcomes")), ("Failures / Exceptions", val.get("exceptions")),
            ("Input Data", val.get("input_data")), ("Output Data", val.get("output_data")),
            ("Data Format", val.get("data_format")), ("Business Rules", val.get("business_rules")),
            ("Assumptions", val.get("assumptions")), ("Constraints", constraints),
            ("Comments", val.get("comments")),
        ])
    sections = [
        ("5.2 Context Diagram", ("systems_technology", "integrations")),
        ("5.3 Data Flow Diagrams", ("processes", "workflows", "integrations")),
        ("5.4 Logical Data Model", ("data",)), ("5.5 Data Dictionary", ("data",)),
        ("5.6 Non-functional Requirements", ("requirements",)),
        ("5.6.1 Interface Requirements", ("users_personas",)),
        ("5.6.2 Hardware Interfaces", ()), ("5.6.3 Software Interfaces", ("systems_technology",)),
        ("5.6.4 Communications Interfaces", ("integrations",)), ("5.6.5 Data Conversion Requirements", ()),
        ("5.7 Security and Safety Requirements", ("compliance", "policies")),
        ("5.7.1 Security and Privacy", ("compliance",)), ("5.7.2 Audit Trail", ()),
        ("5.7.3 Reliability", ()), ("5.7.4 Recoverability", ()), ("5.7.5 System Availability", ()),
        ("5.8 General Performance", ("kpis_metrics",)), ("5.9 Data Retention", ()),
        ("5.10 Error Handling", ()), ("5.11 Validation Rules", ("business_rules",)),
        ("5.12 Conventions / Standards", ("compliance",)), ("5.13 Software Quality Attributes", ()),
        ("6.0 Assumptions and Constraints (Business and Technical)", ()),
        ("6.1 Assumptions and Other Relevant Facts", ("assumptions",)),
        ("6.2 Requirement Constraints and Dependencies", ("constraints", "dependencies")),
        ("7.0 Appendix A - Glossary", ("glossary",)),
        ("Appendix B - Solution Architecture", ("systems_technology",)),
    ]
    overrides = {
        "5.2 Context Diagram": "A reviewed context diagram has not yet been recorded.\n\n" + section(context, "integrations"),
        "5.3 Data Flow Diagrams": "Reviewed data flow diagrams have not yet been recorded.\n\n" + section(context, "processes", "workflows"),
        "5.4 Logical Data Model": fields(context, "data", "entities", "relationships"),
        "5.5 Data Dictionary": data_dictionary(facts),
        "5.6 Non-functional Requirements": fields(context, "requirements", "non_functional_requirements"),
        "5.6.1 Interface Requirements": fields(context, "requirements", "user_requirements", "technical_requirements"),
        "5.6.2 Hardware Interfaces": fields(context, "systems_technology", "infrastructure"),
        "5.6.5 Data Conversion Requirements": fields(context, "data", "data_lifecycle", "data_sources"),
        "5.7.1 Security and Privacy": fields(context, "compliance", "privacy_requirements", "security_requirements"),
        "5.7.2 Audit Trail": fields(context, "compliance", "audit_requirements"),
        "5.9 Data Retention": fields(context, "data", "data_retention") + "\n\n" + fields(context, "compliance", "retention_requirements"),
        "5.10 Error Handling": fields(context, "integrations", "failure_handling") + "\n\n" + fields(context, "processes", "exceptions"),
        "5.11 Validation Rules": fields(context, "business_rules", "validation_rules", "rules", "conditions", "thresholds"),
        "5.12 Conventions / Standards": fields(context, "compliance", "industry_standards", "regulations"),
        "5.13 Software Quality Attributes": fields(context, "requirements", "non_functional_requirements"),
        "6.1 Assumptions and Other Relevant Facts": summary_value(summary, "important_assumptions") + "\n\n" + section(context, "assumptions"),
        "6.2 Requirement Constraints and Dependencies": summary_value(summary, "constraints", "dependencies") + "\n\n" + section(context, "constraints", "dependencies"),
        "Appendix B - Solution Architecture": "A reviewed architecture diagram has not yet been recorded.\n\n" + fields(context, "systems_technology", "architecture"),
    }
    # Measurable targets stay tied to the requirement that supplied them, without sample targets.
    for title, types in (("5.7.1 Security and Privacy", {"security", "privacy"}),
            ("5.7.3 Reliability", {"reliability"}), ("5.7.4 Recoverability", {"recoverability", "recovery"}),
            ("5.7.5 System Availability", {"availability"}), ("5.8 General Performance", {"performance", "capacity"})):
        targets = [f"- **{req_name}**: {constraint_text(c)}" for _, req_name, val in reqs
            for c in val.get("constraints") or [] if isinstance(c, dict) and str(c.get("type", "")).lower() in types]
        if targets:
            overrides[title] = "\n".join(targets)
    for heading, keys in sections:
        level = 2 if heading.startswith(("6.0", "7.0", "Appendix")) else 4 if heading.count(".") >= 2 else 3
        body += f"\n{'#' * level} {heading}\n" + overrides.get(heading, section(context, *keys)) + "\n"
    headings = [line.lstrip("# ") for line in body.splitlines() if line.startswith("##")]
    return front + contents(headings) + body
