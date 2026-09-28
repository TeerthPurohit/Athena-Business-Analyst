"""Focused route checks without a database or external object store."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
import pytest

from agents.business_analyst.api.routes import (
    download_project_deliverable_endpoint,
    generate_deliverable_endpoint,
    get_project_deliverable_endpoint,
)
from agents.business_analyst.facts import BATenantContext


class MemoryStore:
    def __init__(self):
        self.objects = {}

    def put_if_absent(self, key, content, media_type):
        self.objects[key] = content
        return f"s3://test/{key}"

    def get(self, reference):
        return self.objects[reference.removeprefix("s3://test/")]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["brd", "frd", "rtm"])
async def test_generated_deliverable_can_be_reopened_and_downloaded(key):
    ctx = BATenantContext(org_id="org-1", project_id="project-1")
    spec = SimpleNamespace(key=key, org_id=None)
    tenant_spec = SimpleNamespace(key=key, org_id="org-1")
    db = SimpleNamespace(execute=AsyncMock(), add=Mock(), commit=AsyncMock())
    specs_result = Mock()
    specs_result.scalars.return_value.all.return_value = [spec, tenant_spec]
    facts_result = Mock()
    facts_result.scalars.return_value.all.return_value = [SimpleNamespace(seq=7)]
    db.execute.side_effect = [specs_result, facts_result]
    store = MemoryStore()

    with patch("agents.business_analyst.api.routes.render_deliverable", new_callable=AsyncMock) as render, \
         patch("agents.business_analyst.api.routes._get_or_create_chat_source", new_callable=AsyncMock) as chat_source, \
         patch("agents.business_analyst.api.routes.assert_fact", new_callable=AsyncMock):
        chat_source.return_value = SimpleNamespace(id="chat-source")
        render.return_value = "# Project BRD\n"
        generated = await generate_deliverable_endpoint("project-1", key, ctx, db, store)
        render.assert_awaited_once_with(tenant_spec, ctx, db)

    instance = db.add.call_args.args[0]
    assert instance.content_ref.startswith("s3://test/deliverables/org-1/project-1/")
    assert "Project BRD" in generated["content"]
    assert generated["output_format"] == ("pdf" if key in {"brd", "frd"} else "markdown")
    db.execute = AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=instance)))

    with patch("agents.business_analyst.api.routes.is_stale", new_callable=AsyncMock, return_value=False):
        opened = await get_project_deliverable_endpoint("project-1", instance.id, ctx, db, store)
    download = await download_project_deliverable_endpoint("project-1", instance.id, ctx, db, store)
    assert "Project BRD" in opened["content"]
    extension = "pdf" if key in {"brd", "frd"} else "md"
    assert instance.content_ref.endswith("." + extension)
    assert download.body.startswith(b"%PDF-") if extension == "pdf" else download.body == b"# Project BRD\n"
    assert download.headers["content-disposition"] == f'attachment; filename="{key}.{extension}"'
    assert download.headers["content-type"].startswith("application/pdf" if extension == "pdf" else "text/markdown")
    query_params = db.execute.call_args.args[0].compile().params
    assert "project-1" in query_params.values()
    assert "org-1" in query_params.values()


@pytest.mark.asyncio
async def test_retrieval_returns_404_for_instance_outside_project():
    ctx = BATenantContext(org_id="org-1", project_id="project-1")
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=None))))
    store = MemoryStore()

    with pytest.raises(HTTPException) as error:
        await get_project_deliverable_endpoint("project-1", "foreign-instance", ctx, db, store)
    assert error.value.status_code == 404
    assert store.objects == {}


@pytest.mark.asyncio
async def test_legacy_inline_reference_returns_410():
    ctx = BATenantContext(org_id="org-1", project_id="project-1")
    instance = SimpleNamespace(content_ref="inline://brd/1")
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=instance))))

    with pytest.raises(HTTPException) as error:
        await get_project_deliverable_endpoint("project-1", "legacy-instance", ctx, db, MemoryStore())
    assert error.value.status_code == 410
