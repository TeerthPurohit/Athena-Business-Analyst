"""Test Case renderer based only on explicit or testable requirement evidence."""

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.capabilities.projection import REQUIREMENTS_NEEDED, plain, requirements
from agents.business_analyst.facts import BATenantContext, get_facts
from agents.business_analyst.models import BaDeliverableSpec

TITLE = "Test Case Suite"
NEEDS = REQUIREMENTS_NEEDED


def _items(value):
    values = value if isinstance(value, (list, tuple)) else [value]
    return list(dict.fromkeys(text for item in values if (text := plain(item))))


async def render(spec: BaDeliverableSpec, ctx: BATenantContext, session: AsyncSession) -> str | None:
    reqs = requirements(await get_facts(ctx, session))
    if not reqs:
        return None

    cases = []
    needs_design = []
    for _, requirement_id, value in reqs:
        preconditions = _items(value.get("preconditions"))
        trigger = plain(value.get("trigger"))
        outcomes = _items(value.get("outcomes"))
        criteria = _items(value.get("acceptance_criteria"))
        if criteria:
            for index, criterion in enumerate(criteria, 1):
                cases.append((
                    f"{requirement_id}-{index:02d}", preconditions, trigger, outcomes,
                    criterion,
                    "Draft - QA review required" if trigger and outcomes else "Draft - test steps need QA design",
                ))
        elif trigger and outcomes:
            cases.append((requirement_id, preconditions, trigger, outcomes, "", "Draft - QA review required"))
        else:
            needs_design.append(requirement_id)

    md = f"# {TITLE}\n\n"
    md += "Test scenarios use recorded triggers and expected behavior. Each case needs independent QA review before execution.\n\n"
    for case_key, preconditions, trigger, outcomes, criterion, status in cases:
        case_id = f"TC-{case_key}"
        requirement_id = case_key.split("-")[0] + "-" + case_key.split("-")[1] if case_key.startswith("REQ-") else case_key
        md += f"### {case_id} - {requirement_id}\n\n"
        if preconditions:
            md += "**Given:** " + "; ".join(preconditions) + "\n\n"
        if trigger:
            md += f"**When:** {trigger}\n\n"
        if outcomes:
            md += "**Then:** " + "; ".join(outcomes) + "\n\n"
        if criterion:
            md += f"**Acceptance criterion:** {criterion}\n\n"
        md += f"**Status:** {status}\n\n"

    if needs_design:
        md += f"## Requirements needing test design ({len(needs_design)})\n\n"
        md += "No explicit acceptance criterion or complete trigger-and-outcome pair is recorded for these requirements:\n\n"
        md += "".join(f"- {requirement_id}\n" for requirement_id in needs_design)
    if not cases:
        md += "No executable test cases can be drafted from the current evidence. Review the Requirements Register for missing acceptance behavior.\n"
    return md
