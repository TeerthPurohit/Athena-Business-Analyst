"""Database-independent checks for the project deliverable catalog route."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agents.business_analyst.api.routes import list_project_deliverable_catalog_endpoint
from agents.business_analyst.deliverables import DELIVERABLE_CATALOG_SEEDS
from agents.business_analyst.facts import BATenantContext


@pytest.mark.asyncio
async def test_project_catalog_exposes_all_17_and_prefers_tenant_override():
    global_specs = [SimpleNamespace(org_id=None, **item) for item in DELIVERABLE_CATALOG_SEEDS]
    override = SimpleNamespace(**{**DELIVERABLE_CATALOG_SEEDS[0], "org_id": "org-1", "purpose": "Tenant BRD"})
    result = Mock()
    result.scalars.return_value.all.return_value = [override, *global_specs]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    catalog = await list_project_deliverable_catalog_endpoint(
        "project-1", BATenantContext(org_id="org-1", project_id="project-1"), db
    )

    assert len(catalog) == 17
    assert {item["key"] for item in catalog} == {item["key"] for item in DELIVERABLE_CATALOG_SEEDS}
    assert next(item for item in catalog if item["key"] == "brd")["purpose"] == "Tenant BRD"
