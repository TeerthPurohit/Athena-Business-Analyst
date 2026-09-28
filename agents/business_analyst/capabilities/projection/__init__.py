"""Projection renderers for BA OS deliverables (§11.4, §11.5).

CLAUDE.md Non-Negotiables:
- Pure rendering functions of (facts/business context, deliverable_spec) -> markdown/str output.
- rtm, gap_report, change_log are pure scans with ZERO LLM calls.
- Structural renderers generate templates from pre-scored fields with ZERO LLM calls.
- Narrated renderers (brd, options_analysis) use llm_client.get_structured_output over pre-scored fields.
- Honest failure: missing fields render in plain words ("[benefit not yet specified]"), never as
  developer markers or raw ids.
- Gating: a renderer returns None when the facts/context it reads are absent; the dispatcher then
  records one gap fact and returns a plain-language "can't be written yet" page.
"""
import importlib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.business_context import BUSINESS_CONTEXT_STRUCTURE
from agents.business_analyst.clarification import _get_or_create_system_source
from agents.business_analyst.facts import BATenantContext, assert_fact, get_facts
from agents.business_analyst.models import BaDeliverableSpec, BaFact, BaProject

REQUIREMENTS_NEEDED = "requirements captured from its sources"


def label(key: Any) -> str:
    """snake_case key -> readable label, keeping acronyms such as SLAs or KPIs intact."""
    text = str(key).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def plain(value: Any) -> str:
    """Stored value -> readable text (no raw JSON); empty values become ''."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return ", ".join(f"{label(k)}: {text}" for k, v in value.items() if (text := plain(v)))
    if isinstance(value, (list, tuple)):
        return "; ".join(text for text in map(plain, value) if text)
    return str(value).strip()


def unspecified(what: str) -> str:
    return f"[{what} not yet specified]"


def filled(value: dict, key: str) -> str:
    return plain(value.get(key)) or unspecified(label(key).lower())


def requirements(facts: list[BaFact]) -> list[tuple[str, str, dict]]:
    """(subject_key, label, value) per current requirement, in capture order.

    Only specified_as facts are requirements (derived_from facts are their evidence links). The
    REQ-001... labels come from this one ordering, so every deliverable numbers them the same way.
    """
    by_key: dict[str, dict] = {}
    for fact in facts:
        if fact.subject_type == "Requirement" and fact.predicate == "specified_as" and isinstance(fact.value, dict):
            by_key[fact.subject_key] = fact.value
    return [(key, f"REQ-{n:03d}", value) for n, (key, value) in enumerate(by_key.items(), 1)]


def user_story(requirement: dict) -> str:
    """requirements.render_user_story's sentence, with plain-language blanks instead of markers."""
    return (
        f"As a {filled(requirement, 'stakeholder')}, "
        f"I want to {filled(requirement, 'task')}, "
        f"so that {filled(requirement, 'benefit')}."
    )


def constraint_text(constraint: Any) -> str:
    if not isinstance(constraint, dict):
        return plain(constraint)
    text = f"{label(constraint.get('type') or 'constraint')}: {plain(constraint.get('value'))}"
    return text if constraint.get("measurable") else f"{text} (not yet measurable)"


async def business_context(ctx: BATenantContext, session: AsyncSession) -> dict[str, Any]:
    """The project's business context from settings["project_summary"]; {} when absent."""
    project = await session.get(BaProject, ctx.project_id)
    if project is None or project.org_id != ctx.org_id:
        return {}
    summary = (project.settings or {}).get("project_summary")
    context = summary.get("business_context") if isinstance(summary, dict) else None
    return context if isinstance(context, dict) else {}


def _key_field(section: str) -> str:
    return (BUSINESS_CONTEXT_STRUCTURE.get(section) or ("name",))[0]


def context_records(context: dict, section: str) -> list[dict]:
    """Populated records of an entity section (a list of dicts); tolerates the legacy
    single dict-of-fields shape and bare strings."""
    value = context.get(section)
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    records = [item if isinstance(item, dict) else {_key_field(section): item} for item in value]
    return [record for record in records if plain(record)]


def field_lines(fields: dict, skip: str | None = None) -> str:
    """'- **Label**: text' per populated field; multi-item lists become nested bullets."""
    out = ""
    for field, value in fields.items():
        if field == skip:
            continue
        items = [text for text in map(plain, value if isinstance(value, list) else [value]) if text]
        if len(items) == 1:
            out += f"- **{label(field)}**: {items[0]}\n"
        elif items:
            out += f"- **{label(field)}**:\n" + "".join(f"  - {text}\n" for text in items)
    return out


def records_markdown(context: dict, section: str, item_name: str, level: int = 3) -> str:
    """A heading plus populated fields per record of an entity section; '' when it has none."""
    key = _key_field(section)
    return "".join(
        f"{'#' * level} {plain(record.get(key)) or f'{item_name} {n}'}\n{field_lines(record, skip=key)}\n"
        for n, record in enumerate(context_records(context, section), 1)
    )


async def render_deliverable(
    spec: BaDeliverableSpec,
    ctx: BATenantContext,
    session: AsyncSession,
) -> str:
    """Dispatches rendering; when the renderer's inputs are absent, records the gap once and says so plainly."""
    try:
        mod = importlib.import_module(f"agents.business_analyst.capabilities.projection.{spec.key}")
    except ModuleNotFoundError:
        return f"# {label(spec.key)}\n\nThis deliverable type is not available yet."

    content = await mod.render(spec, ctx, session)
    if content is not None:
        return content

    # Record the missing input once per deliverable, on the project's reusable system source.
    subject_key = f"gap_missing_input_{spec.key}"
    if not await get_facts(ctx, session, subject_key=subject_key, predicate="gap"):
        source = await _get_or_create_system_source(ctx, session)
        await assert_fact(
            ctx,
            session,
            subject_type="Gap",
            subject_key=subject_key,
            predicate="gap",
            value={
                "deliverable_key": spec.key,
                "reason": f"The {mod.TITLE} can't be written until the project has {mod.NEEDS}.",
            },
            source_id=source.id,
            asserted_by="projection_engine",
        )
    return (
        f"# {mod.TITLE}\n\nThis draft can't be written yet: the project has no {mod.NEEDS} so far. "
        "Add a source document or share more detail in chat, then generate it again."
    )
