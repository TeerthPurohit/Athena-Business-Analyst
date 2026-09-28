"""Evidence-backed document sections shared by the reference BRD and FRD formats."""
from pathlib import PurePosixPath

from agents.business_analyst.capabilities.projection import context_records, field_lines, plain, records_markdown
from agents.business_analyst.models import BaProject, BaSource

MISSING = "Not yet specified in the project record."


def table(headers, rows, *, missing=MISSING):
    def cell(value):
        return (plain(value) or missing).replace("|", "\\|").replace("\n", "<br>")
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "".join(
        "| " + " | ".join(cell(value) for value in row) + " |\n" for row in rows
    )


async def project_document_context(ctx, session):
    project = await session.get(BaProject, ctx.project_id)
    if project is None or project.org_id != ctx.org_id:
        return "Project", {}, {}, {}
    settings = project.settings or {}
    summary = settings.get("project_summary") or {}
    summary = summary if isinstance(summary, dict) else {}
    context = summary.get("business_context") or {}
    control = settings.get("document_control") or {}
    return getattr(project, "name", "Project"), summary, context if isinstance(context, dict) else {}, control if isinstance(control, dict) else {}


def fields(context, section_key, *keys):
    value = context.get(section_key) or {}
    records = value if isinstance(value, list) else [value]
    return "\n\n".join(field_lines({key: record.get(key) for key in keys}).rstrip()
        for record in records if isinstance(record, dict) and any(plain(record.get(key)) for key in keys)) or MISSING


def data_dictionary(facts):
    entities = {f.subject_key: f.value for f in facts
        if f.subject_type == "entity" and f.predicate == "described_as" and isinstance(f.value, dict)}
    rows = []
    for name, value in entities.items():
        attrs = value.get("attributes") or []
        attrs = attrs if isinstance(attrs, list) else [attrs]
        for attr in attrs or [None]:
            detail = attr if isinstance(attr, dict) else {"name": attr}
            rows.append((name, detail.get("business_name"), detail.get("name"), detail.get("data_type"), detail.get("size"), detail.get("description")))
    return table(["Table / Entity", "Business Name", "Column / Attribute", "Data Type", "Size", "Description"], rows or [(None,) * 6])


def reporting_fields(facts):
    entities = {f.subject_key: f.value for f in facts
        if f.subject_type == "entity" and f.predicate == "described_as" and isinstance(f.value, dict)}
    rows = []
    for name, value in entities.items():
        attributes = value.get("attributes") or []
        attributes = attributes if isinstance(attributes, list) else [attributes]
        for attribute in attributes:
            detail = attribute if isinstance(attribute, dict) else {"name": attribute}
            field_name = plain(detail.get("name"))
            rows.append((len(rows) + 1, f"{name}.{field_name}" if field_name else name, detail.get("description")))
    return table(["Field Number", "Field Name", "Field Description"], rows or [(None, None, None)])


def section(context, *keys):
    parts = []
    for key in keys:
        value = context.get(key)
        body = records_markdown(context, key, "Entry", level=4) if isinstance(value, list) else field_lines(value) if isinstance(value, dict) else plain(value)
        if body:
            parts.append(body.rstrip())
    return "\n\n".join(parts) or MISSING


def summary_value(summary, *keys):
    return "\n\n".join(plain(summary.get(key)) for key in keys if plain(summary.get(key))) or MISSING


async def front_matter(title, name, context, control, facts, ctx, session, frd=False, max_sources=30):
    md = f"# {title}\n\n**Project:** {name}\n\n**Status:** Draft - subject to review and approval.\n\n"
    information = [
        (field_label, control.get(key)) for key, field_label in (
            ("version", "Current Version"), ("owner", "Owner"), ("updated_at", "Date Last Updated"),
            ("updated_by", "Last Updated By"), ("author", "Author"), ("created_at", "Date Created"),
            ("approved_by", "Approved By"), ("approval_date", "Approval Date"),
        )
        if control.get(key) not in (None, "", [], {})
    ]
    if information:
        md += "## Document Information\n" + table(["Item", "Description"], information) + "\n"
    history = [
        r for r in (control.get("revision_history") or [])
        if isinstance(r, dict) and any(value not in (None, "", [], {}) for value in r.values())
    ]
    if history:
        md += "## " + ("Revision History" if frd else "Document Control") + "\n" + table(
            ["Version", "Date", "Author", "Description"],
            [(r.get("version"), r.get("date"), r.get("author"), r.get("description")) for r in history],
        ) + "\n"
    if frd:
        approvals = [
            r for r in (control.get("approvals") or [])
            if isinstance(r, dict) and any(value not in (None, "", [], {}) for value in r.values())
        ]
        if approvals:
            md += "## Document Approvals History\n" + table(
                ["Role", "Name", "Signature", "Date"],
                [(r.get("role"), r.get("name"), r.get("signature"), r.get("date")) for r in approvals],
            ) + "\n"
    else:
        stakeholders = [
            r for r in context_records(context, "stakeholders")
            if any(value not in (None, "", [], {}) for value in r.values())
        ]
        if stakeholders:
            md += "## Distribution List\n" + table(
                ["Name", "Department", "Role"],
                [(r.get("name"), r.get("department"), r.get("role")) for r in stakeholders],
            ) + "\n"
    sources = []
    source_ids = list(dict.fromkeys(
        getattr(fact, "source_id", None) for fact in facts if getattr(fact, "source_id", None)
    ))
    for source_id in source_ids[:max_sources]:
        source = await session.get(BaSource, source_id)
        if source and source.org_id == ctx.org_id and source.project_id == ctx.project_id:
            ref = str(source.ref or "").replace("\\", "/")
            title = PurePosixPath(ref).name if ref else "Recorded project context"
            sources.append((f"REF-{len(sources)+1:03d}", title, source.kind))
    references = (
        table(["Ref", "Title", "Source type"], sources)
        if sources else "No source documents have been linked to the current project record.\n"
    )
    remaining = max(0, len(source_ids) - max_sources)
    if remaining:
        references += f"\n{remaining} additional source records are linked in the Requirements Register.\n"
    return md, references


def contents(headings):
    return "## Table of Contents\n" + "\n".join(f"- {heading}" for heading in headings) + "\n\n"
