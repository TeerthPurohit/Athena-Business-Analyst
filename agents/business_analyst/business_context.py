"""Canonical evidence-backed business context shared by BA analysis and deliverables."""

import json
from typing import Any


BUSINESS_CONTEXT_STRUCTURE: dict[str, tuple[str, ...]] = {
    "organization": ("company_name", "company_description", "industry", "sub_industry", "company_size", "locations", "countries", "founded_date", "business_model", "revenue_model", "ownership", "parent_company", "subsidiaries", "departments", "business_units", "organization_structure"),
    "business": ("mission", "vision", "values", "business_goals", "strategic_objectives", "strategic_priorities", "business_problems", "business_opportunities", "business_model", "value_proposition", "competitive_advantage", "revenue_streams", "cost_centers", "growth_strategy"),
    "strategy": ("strategic_objectives", "strategic_priorities", "growth_strategy", "competitive_advantage", "value_proposition"),
    "products_services": ("product_name", "description", "category", "features", "capabilities", "pricing", "plans", "target_market", "value_proposition", "product_lifecycle", "product_dependencies", "product_constraints", "roadmap"),
    "customers": ("customer_segments", "customer_types", "customer_profiles", "personas", "customer_needs", "customer_pain_points", "customer_goals", "customer_journey", "customer_lifecycle", "acquisition_channels", "retention", "churn", "feedback"),
    "users_personas": ("user_name", "role", "department", "responsibilities", "goals", "needs", "pain_points", "tasks", "permissions", "technical_skill", "frequency_of_use", "user_journey"),
    "stakeholders": ("name", "role", "department", "responsibilities", "authority", "influence", "interests", "expectations", "concerns", "decision_power", "communication_preference", "approval_requirements"),
    "organization_structure": ("departments", "teams", "roles", "reporting_lines", "ownership", "responsibilities", "decision_hierarchy", "approval_hierarchy", "cross_functional_relationships"),
    "processes": ("process_name", "purpose", "owner", "trigger", "inputs", "outputs", "actors", "steps", "decisions", "handoffs", "systems_used", "exceptions", "business_rules", "SLAs", "KPIs", "current_state", "future_state", "pain_points"),
    "workflows": ("workflow_name", "trigger", "actors", "steps", "decisions", "handoffs", "systems_used", "exceptions", "current_state", "future_state", "automation_opportunities"),
    "projects": ("project_name", "description", "objective", "scope", "status", "owner", "team", "stakeholders", "timeline", "milestones", "deliverables", "budget", "dependencies", "risks", "issues", "requirements", "decisions"),
    "requirements": ("business_requirements", "functional_requirements", "non_functional_requirements", "user_requirements", "technical_requirements", "reporting_requirements", "security_requirements", "integration_requirements", "data_requirements", "regulatory_requirements", "acceptance_criteria"),
    "business_rules": ("rules", "conditions", "exceptions", "thresholds", "calculations", "eligibility_rules", "approval_rules", "validation_rules", "authorization_rules"),
    "systems_technology": ("systems", "applications", "databases", "infrastructure", "architecture", "vendors", "technology_stack", "versions", "environments", "APIs", "authentication", "permissions", "limitations", "technical_debt"),
    "integrations": ("source_system", "target_system", "integration_type", "data_transferred", "frequency", "direction", "API", "authentication", "dependencies", "failure_handling", "ownership"),
    "data": ("data_sources", "entities", "attributes", "relationships", "data_dictionary", "data_owners", "data_quality", "data_volume", "data_frequency", "data_lifecycle", "data_retention", "data_sensitivity", "PII", "data_access", "data_governance"),
    "analytics": ("reports", "dashboards", "metrics", "dimensions", "filters", "calculations", "data_sources", "refresh_frequency", "audience", "reporting_requirements"),
    "kpis_metrics": ("metric_name", "definition", "formula", "unit", "target", "baseline", "current_value", "owner", "data_source", "frequency", "thresholds"),
    "financials": ("budget", "revenue", "costs", "operating_costs", "project_cost", "ROI", "expected_savings", "financial_targets", "pricing", "unit_economics"),
    "market": ("market", "market_size", "segments", "trends", "customer_demand", "market_drivers", "market_constraints", "regulatory_environment", "opportunities"),
    "competitors": ("name", "products", "features", "pricing", "positioning", "strengths", "weaknesses", "market_share", "differentiators"),
    "risks": ("risk", "probability", "impact", "severity", "owner", "mitigation", "contingency", "status"),
    "constraints": ("budget_constraints", "timeline_constraints", "technical_constraints", "resource_constraints", "organizational_constraints", "regulatory_constraints", "geographical_constraints", "vendor_constraints", "security_constraints"),
    "compliance": ("regulations", "laws", "industry_standards", "certifications", "privacy_requirements", "security_requirements", "retention_requirements", "audit_requirements", "contractual_requirements"),
    "policies": ("company_policies", "department_policies", "security_policies", "HR_policies", "financial_policies", "approval_policies", "data_policies", "operational_policies"),
    "sops": ("SOPs", "process_documents", "technical_documents", "product_documents", "training_documents", "contracts", "policies", "manuals", "previous_BRDs", "previous_PRDs", "previous_requirements"),
    "decisions": ("decision", "date", "decision_maker", "reason", "alternatives_considered", "status", "affected_areas", "related_requirements"),
    "assumptions": ("assumption", "source", "confidence", "impact_if_false", "validation_status"),
    "dependencies": ("dependency", "type", "owner", "dependent_on", "status", "deadline", "impact"),
    "timeline": ("start_date", "end_date", "deadline", "milestones", "phases", "dependencies", "release_dates", "approval_dates"),
    "resources": ("people", "teams", "skills", "capacity", "budget", "tools", "vendors", "infrastructure"),
    "documents": ("SOPs", "process_documents", "technical_documents", "product_documents", "training_documents", "contracts", "policies", "manuals", "previous_BRDs", "previous_PRDs", "previous_requirements"),
    "communication": ("communication_channels", "meeting_cadence", "reporting_cadence", "stakeholder_preferences", "escalation_process", "approval_process"),
    "history": ("previous_projects", "previous_decisions", "previous_requirements", "previous_failures", "previous_changes", "historical_metrics", "past_incidents", "past_customer_feedback"),
    "preferences": ("preferred_tools", "preferred_technologies", "preferred_document_formats", "naming_conventions", "documentation_style", "approval_style", "communication_style"),
}

# Sections describing many distinct things (one record per stakeholder, process, risk, ...),
# with the field(s) that identify a record. Every other section is a single field -> value map.
ENTITY_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "products_services": ("product_name",),
    "users_personas": ("user_name",),
    "stakeholders": ("name",),
    "processes": ("process_name",),
    "workflows": ("workflow_name",),
    "projects": ("project_name",),
    "integrations": ("source_system", "target_system"),
    "kpis_metrics": ("metric_name",),
    "competitors": ("name",),
    "risks": ("risk",),
    "decisions": ("decision",),
    "assumptions": ("assumption",),
    "dependencies": ("dependency",),
}


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _lookup(mapping: dict, name: str) -> Any:
    """Exact key first, then case-insensitive — models often lowercase keys like SLAs or PII."""
    if name in mapping:
        return mapping[name]
    lowered = name.lower()
    return next((value for key, value in mapping.items() if isinstance(key, str) and key.lower() == lowered), None)


def _record(fields: tuple[str, ...], raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return {field: _lookup(raw, field) for field in fields}


def record_key(section: str, record: dict[str, Any]) -> str | None:
    """Stable identity for an entity record, or None when its key field is unknown."""
    parts = [record.get(field) for field in ENTITY_KEY_FIELDS[section]]
    if any(is_empty(part) or not isinstance(part, (str, int, float)) for part in parts):
        return None
    return " -> ".join(" ".join(str(part).split()).lower() for part in parts)


def normalize_business_context(value: Any) -> dict[str, Any]:
    """Return every context section and field; unknown scalars are None, entity sections are record lists."""
    supplied = value if isinstance(value, dict) else {}
    normalized: dict[str, Any] = {}
    for section, fields in BUSINESS_CONTEXT_STRUCTURE.items():
        raw = _lookup(supplied, section)
        if section in ENTITY_KEY_FIELDS:
            items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
            records = (_record(fields, item) for item in items if isinstance(item, dict))
            normalized[section] = [record for record in records if not all(is_empty(v) for v in record.values())]
        else:
            normalized[section] = _record(fields, raw)
    return normalized


def empty_business_context() -> dict[str, Any]:
    return normalize_business_context({})


def _merge(current: Any, new: Any) -> Any:
    """Scalars: the latest evidence wins. Lists: union in first-seen order."""
    if is_empty(new):
        return current
    if not isinstance(current, list) and not isinstance(new, list):
        return new
    merged: list[Any] = []
    seen: set[str] = set()
    for group in (current, new):
        for item in group if isinstance(group, list) else [] if is_empty(group) else [group]:
            marker = json.dumps(item, sort_keys=True, default=str).lower()
            if marker not in seen:
                seen.add(marker)
                merged.append(item)
    return merged


def fold_business_context(facts: list[Any]) -> dict[str, Any]:
    """Build the context deterministically from validated business_context facts (seq order).

    Entity facts carry {"section", "record"} keyed by subject_key; flat facts carry
    {"section", "field", "value"}. Flat facts in entity sections predate record support and are
    folded into one record per section so existing projects keep their context.
    """
    context = empty_business_context()
    records: dict[str, dict[str, dict[str, Any]]] = {section: {} for section in ENTITY_KEY_FIELDS}
    for fact in facts:
        value = fact.value if isinstance(fact.value, dict) else None
        if fact.subject_type != "business_context" or value is None:
            continue
        section = value.get("section")
        fields = BUSINESS_CONTEXT_STRUCTURE.get(section)
        if fields is None:
            continue
        if section in ENTITY_KEY_FIELDS:
            record = value.get("record")
            if not isinstance(record, dict):
                record, key = {value.get("field"): value.get("value")}, f"{section}:"
            else:
                key = fact.subject_key
            merged = records[section].setdefault(key, {field: None for field in fields})
            for field in fields:
                merged[field] = _merge(merged[field], _lookup(record, field))
        elif value.get("field") in context[section]:
            context[section][value["field"]] = _merge(context[section][value["field"]], value.get("value"))
    for section, by_key in records.items():
        context[section] = list(by_key.values())
    return context
