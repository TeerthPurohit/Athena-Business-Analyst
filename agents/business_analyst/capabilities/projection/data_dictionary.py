"""Data Dictionary renderer with only evidenced field definitions."""

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import plain
from agents.business_analyst.capabilities.projection.document_template import table
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Data Dictionary"
NEEDS = "business entities identified in its sources"


def _attributes(value):
    if isinstance(value, dict):
        return [(str(key), plain(detail)) for key, detail in value.items() if plain(detail)]
    if isinstance(value, (list, tuple)):
        rows = []
        for index, item in enumerate(value, 1):
            if isinstance(item, dict):
                field_name = item.get("name") or item.get("field") or item.get("key") or f"Attribute {index}"
                description = item.get("description") or item.get("type") or item.get("definition")
                rows.append((plain(field_name), plain(description)))
            elif plain(item):
                rows.append((f"Attribute {index}", plain(item)))
        return rows
    text = plain(value)
    return [("Recorded attributes", text)] if text else []


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    facts = await get_facts(ctx, session)
    entities = {
        fact.subject_key: fact.value if isinstance(fact.value, dict) else {}
        for fact in facts
        if fact.subject_type == "entity" and fact.predicate == "described_as"
    }
    if not entities:
        return None

    md = f"# {TITLE}\n\n"
    md += f"{len(entities)} current entities are represented below. Only attributes recorded in the project evidence are listed.\n\n"
    incomplete = []
    for name, value in entities.items():
        entity_type = plain(value.get("type"))
        attributes = _attributes(value.get("attributes"))
        md += f"### {name}\n\n"
        if entity_type:
            md += f"**Entity type:** {entity_type}\n\n"
        if attributes:
            md += table(["Field / Attribute", "Recorded Definition or Type"], attributes, missing="")
            md += "\n"
        missing = []
        if not entity_type:
            missing.append("entity type")
        if not attributes:
            missing.append("attribute definitions")
        if missing:
            incomplete.append((name, missing))

    if incomplete:
        md += f"## Definitions needing confirmation ({len(incomplete)})\n\n"
        md += "".join(f"- {name}: confirm {', '.join(missing)}.\n" for name, missing in incomplete)
    return md
