"""BRD Renderer (Business Requirements Document).

Narrated renderer: the executive summary comes from llm_client.get_structured_output with the
DB prompt ba_brd_narrative_v2; business context and requirements are rendered deterministically.
"""
import logging

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import (
    REQUIREMENTS_NEEDED,
    constraint_text,
    field_lines,
    label,
    records_markdown,
    requirements,
    user_story,
)
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec
from agents.business_analyst.llm_client import get_structured_output
from models.agent_prompt import fetch_prompt
from agents.business_analyst.capabilities.projection.document_template import (
    contents, fields, front_matter, project_document_context, reporting_fields, section, summary_value, table,
)

logger = logging.getLogger(__name__)

TITLE = "Business Requirements Document (BRD)"
NEEDS = REQUIREMENTS_NEEDED

_SECTION_TITLES = {
    "products_services": "Products and services",
    "users_personas": "Users and personas",
    "systems_technology": "Systems and technology",
    "kpis_metrics": "KPIs and metrics",
    "sops": "SOPs",
}


class BRDNarrative(BaseModel):
    executive_summary: str


def _business_context_markdown(context: dict) -> str:
    """Only populated fields, readable labels, no raw JSON."""
    out = ""
    for section, value in context.items():
        if isinstance(value, list):
            body = records_markdown(context, section, "Entry", level=4)
        elif isinstance(value, dict):
            body = field_lines(value)
        else:
            continue
        if body:
            out += f"### {_SECTION_TITLES.get(section) or label(section)}\n{body.rstrip()}\n\n"
    return out


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    reqs = requirements(facts)
    if not reqs:
        return None

    name, summary, context, control = await project_document_context(ctx, session)
    context_md = _business_context_markdown(context).rstrip()
    req_md = "\n".join(
        f"- **{name}** ({label(value.get('category') or 'uncategorized')}): {user_story(value)}"
        for _, name, value in reqs
    )

    try:
        narrative = await get_structured_output(
            system_prompt=await fetch_prompt("9", "ba_brd_narrative_v2"),
            user_prompt=f"Business context:\n{context_md or 'None recorded.'}\n\nRequirements:\n{req_md}",
            response_model=BRDNarrative,
            agent_id="9",
            name="write-brd-summary",
            session_id=ctx.project_id,
        )
        exec_summary = narrative.executive_summary
    except Exception as exc:
        logger.warning("BA_BRD_NARRATIVE_DEGRADED project_id=%s reason=%s", ctx.project_id, exc)
        exec_summary = "The executive summary could not be written just now. Regenerate this draft to try again."

    md, references = await front_matter(TITLE, name, context, control, facts, ctx, session)
    md += "## Reference Documents\n" + references + "\n"
    md += contents(["1 Introduction", "1.1 Purpose and audience", "1.2 Scope", "1.3 Definitions", "2 Functional requirements", "2.1 Overview of the project (Business Purpose)", "2.2 Overview of the system", "2.3 Reporting principles", "2.4 Data Exchange and Data Validation", "3 Non-functional requirements", "4 Annex I - Data fields"])
    md += "## 1 Introduction\n\n### 1.1 Purpose and audience of this document\nThis document records the business requirements for " + name + ". It supports stakeholder review and provides the input to functional design.\n\n"
    md += "### 1.2 Scope\n\n#### 1.2.1 In scope\n" + summary_value(summary, "functional_scope", "in_scope_features", "must_have_features", "should_have_features") + "\n\n"
    md += "#### 1.2.2 Out of scope\n" + summary_value(summary, "out_of_scope_features", "wont_have_features") + "\n\n"
    md += "### 1.3 Definitions\n" + section(context, "glossary") + "\n\n"
    md += f"## 2 Functional requirements\n\n### 2.1 Overview of the project (Business Purpose)\n\n#### Executive Summary\n{exec_summary}\n\n" + summary_value(summary, "project_purpose", "problem_statement", "business_goals") + "\n\n"
    md += "### 2.2 Overview of the system\n" + section(context, "systems_technology", "products_services", "processes") + "\n\n"
    md += "### 2.3 Reporting principles\n" + section(context, "business_rules", "analytics") + "\n\n"
    md += "### 2.4 Data Exchange and Data Validation\n\n#### 2.4.1 Data Exchange\n" + section(context, "integrations") + "\n\n"
    md += "#### 2.4.2 Data validation and error management\n" + section(context, "business_rules", "data") + "\n\n"
    md += "##### 2.4.2.1 File validation rules\n" + fields(context, "business_rules", "validation_rules") + "\n\n"
    md += "##### 2.4.2.2 File naming convention\n" + fields(context, "preferences", "naming_conventions") + "\n\n"
    md += "### 2.5 Requirements Register\n" + table(["Requirement", "Category", "Business requirement", "Business benefit"],
        [(label_, label(value.get("category") or "uncategorized"), user_story(value), value.get("benefit")) for _, label_, value in reqs]) + "\n"
    md += "## 3 Non-functional requirements\n" + fields(context, "requirements", "non_functional_requirements") + "\n\n" + section(context, "compliance", "constraints") + "\n\n"
    for _, _, value in reqs:
        if value.get("category") == "nonfunctional":
            targets = "; ".join(filter(None, map(constraint_text, value.get("constraints") or [])))
            md += f"- {user_story(value)}" + (f" Targets: {targets}" if targets else "") + "\n"
    md += "## 4 Annex I - Data fields\n" + reporting_fields(facts) + "\n" + section(context, "data") + "\n\n"
    md += "### Business Context\n" + (context_md or "No business context has been recorded for this project yet.") + "\n"
    return md
