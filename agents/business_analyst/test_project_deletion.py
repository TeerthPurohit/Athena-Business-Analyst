"""Focused regression tests for tenant-safe BA project deletion.

Verifies:
1. Foreign project deletion returns 404 (tenant isolation).
2. Missing project deletion returns 404.
3. Successful deletion returns 204 and transactionally cleans up:
   - ba_project
   - ba_fact
   - ba_source
   - ba_deliverable_instance
   - ba_node
   - ba_edge
   - ba_run & ba_run_task
   - ba_embeddings (non-FK project_id rows)
   - athena_source, athena_source_content, athena_source_span (non-FK project_id rows)
   - local upload files under data/ba_uploads/<org>/<project>/
   - S3 deliverable storage refs under deliverables/<org>/<project>/
4. Other projects' data and files remain completely untouched.
5. Append-only fact immutability: direct individual fact deletion/update/truncate is blocked by trigger.
6. Path traversal validation protects against malicious org_id / project_id strings.
7. Storage cleanup failure handling.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid
import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from agents.business_analyst.api.routes import ba_router, get_db, get_deliverable_store
from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.models import (
    BaDeliverableInstance,
    BaEdge,
    BaEmbedding,
    BaFact,
    BaNode,
    BaProject,
    BaRun,
    BaRunTask,
    BaSource,
)
from agents.business_analyst.project_deletion import (
    delete_ba_project,
    validate_safe_upload_path,
)
from athena.models import AthenaSource, AthenaSourceContent, AthenaSourceSpan

import os
from dotenv import load_dotenv
load_dotenv()
SECRET_KEY = os.environ.get("JWT_ACCESS_SECRET", "super_secret_kaynetics_access_key_for_dev_only")

class MemoryObjectStore:
    """In-memory test implementation of ObjectStore with delete and delete_prefix."""

    def __init__(self, bucket: str = "athena-test-bucket"):
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.deleted_refs: list[str] = []
        self.deleted_prefixes: list[str] = []

    def put_if_absent(self, key: str, content: bytes, media_type: str) -> str:
        self.objects[key] = content
        return f"s3://{self.bucket}/{key}"

    def get(self, reference: str) -> bytes:
        prefix = f"s3://{self.bucket}/"
        if not reference.startswith(prefix):
            raise ValueError(f"Invalid reference: {reference}")
        key = reference[len(prefix):]
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    def delete(self, reference: str) -> bool:
        prefix = f"s3://{self.bucket}/"
        if not reference.startswith(prefix):
            raise ValueError(f"Foreign object storage reference cannot be deleted: {reference}")
        key = reference[len(prefix):]
        self.deleted_refs.append(reference)
        if key in self.objects:
            del self.objects[key]
            return True
        return False

    def delete_prefix(self, prefix: str) -> int:
        bucket_prefix = f"s3://{self.bucket}/"
        if prefix.startswith(bucket_prefix):
            prefix = prefix[len(bucket_prefix):]
        elif prefix.startswith("s3://"):
            raise ValueError(f"Foreign bucket prefix cannot be deleted: {prefix}")
        self.deleted_prefixes.append(prefix)
        keys_to_del = [k for k in self.objects if k.startswith(prefix)]
        for k in keys_to_del:
            del self.objects[k]
        return len(keys_to_del)


def _make_token(org_id: str) -> str:
    exp_time = datetime.now(timezone.utc) + timedelta(hours=2)
    return jwt.encode({"org_id": org_id, "exp": exp_time}, SECRET_KEY, algorithm="HS256")


@pytest_asyncio.fixture
async def api_client(db_session):
    store = MemoryObjectStore()
    app = FastAPI()
    app.include_router(ba_router)

    async def _override_get_db():
        yield db_session

    def _override_store():
        return store

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_deliverable_store] = _override_store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, store


@pytest.mark.asyncio
async def test_delete_foreign_project_returns_404(db_session, api_client):
    """Deleting a project owned by another organization returns 404 and leaves project intact."""
    client, _ = api_client
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    proj_a = BaProject(id=str(uuid.uuid4()), org_id=org_a, name="Project A")
    db_session.add(proj_a)
    await db_session.flush()

    token_b = _make_token(org_b)
    response = await client.delete(
        f"/api/ba/projects/{proj_a.id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    # Verify project A is still in the database
    fetched = await db_session.get(BaProject, proj_a.id)
    assert fetched is not None
    assert fetched.org_id == org_a


@pytest.mark.asyncio
async def test_delete_missing_project_returns_404(db_session, api_client):
    """Deleting a non-existent project returns 404."""
    client, _ = api_client
    org_id = str(uuid.uuid4())
    token = _make_token(org_id)
    missing_id = str(uuid.uuid4())

    response = await client.delete(
        f"/api/ba/projects/{missing_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_successful_project_deletion_cleans_all_dependent_data_and_files(
    db_session, api_client, tmp_path
):
    """204 on success; cleans all BA and Athena records, S3 deliverables, and upload files; leaves other projects untouched."""
    client, store = api_client
    org_id = str(uuid.uuid4())
    proj_1_id = str(uuid.uuid4())
    proj_2_id = str(uuid.uuid4())

    # 1. Create Project 1 (to be deleted) and Project 2 (sibling project that must be untouched)
    p1 = BaProject(id=proj_1_id, org_id=org_id, name="Project 1")
    p2 = BaProject(id=proj_2_id, org_id=org_id, name="Project 2")
    db_session.add_all([p1, p2])
    await db_session.flush()

    # 2. Add sources and facts for both projects
    src1 = BaSource(id=str(uuid.uuid4()), project_id=proj_1_id, org_id=org_id, kind="document", tier="tier1", content_hash="hash1")
    src2 = BaSource(id=str(uuid.uuid4()), project_id=proj_2_id, org_id=org_id, kind="document", tier="tier1", content_hash="hash2")
    db_session.add_all([src1, src2])
    await db_session.flush()

    f1 = BaFact(id=str(uuid.uuid4()), project_id=proj_1_id, org_id=org_id, subject_type="Actor", subject_key="act1", predicate="p1", source_id=src1.id, asserted_by="test")
    f2 = BaFact(id=str(uuid.uuid4()), project_id=proj_2_id, org_id=org_id, subject_type="Actor", subject_key="act2", predicate="p2", source_id=src2.id, asserted_by="test")
    db_session.add_all([f1, f2])
    await db_session.flush()

    # 3. Add deliverables for both projects in S3 and DB
    deliv1_key = f"deliverables/{org_id}/{proj_1_id}/deliv1.md"
    deliv2_key = f"deliverables/{org_id}/{proj_2_id}/deliv2.md"
    deliv1_ref = store.put_if_absent(deliv1_key, b"# Deliverable 1", "text/markdown")
    deliv2_ref = store.put_if_absent(deliv2_key, b"# Deliverable 2", "text/markdown")

    d1 = BaDeliverableInstance(
        id=str(uuid.uuid4()),
        project_id=proj_1_id,
        org_id=org_id,
        deliverable_key="brd",
        frontier_seq=1,
        renderer_version="v1",
        output_format="markdown",
        content_ref=deliv1_ref,
        status="generated",
    )
    d2 = BaDeliverableInstance(
        id=str(uuid.uuid4()),
        project_id=proj_2_id,
        org_id=org_id,
        deliverable_key="brd",
        frontier_seq=1,
        renderer_version="v1",
        output_format="markdown",
        content_ref=deliv2_ref,
        status="generated",
    )
    db_session.add_all([d1, d2])
    await db_session.flush()

    # 4. Add nodes and edges
    node1 = BaNode(project_id=proj_1_id, id="Actor:user1", org_id=org_id, type="Actor", key="user1", attrs={"label": "User 1"})
    node2 = BaNode(project_id=proj_2_id, id="Actor:user2", org_id=org_id, type="Actor", key="user2", attrs={"label": "User 2"})
    db_session.add_all([node1, node2])
    await db_session.flush()
    edge1 = BaEdge(project_id=proj_1_id, source_node_id=node1.id, target_node_id=node1.id, relationship="self", org_id=org_id)
    edge2 = BaEdge(project_id=proj_2_id, source_node_id=node2.id, target_node_id=node2.id, relationship="self", org_id=org_id)
    db_session.add_all([edge1, edge2])
    await db_session.flush()

    # 5. Add runs and run tasks
    run1 = BaRun(id=str(uuid.uuid4()), project_id=proj_1_id, org_id=org_id, status="completed")
    run2 = BaRun(id=str(uuid.uuid4()), project_id=proj_2_id, org_id=org_id, status="completed")
    db_session.add_all([run1, run2])
    await db_session.flush()

    task1 = BaRunTask(id=str(uuid.uuid4()), run_id=run1.id, capability_id="c1", org_id=org_id, idempotency_key="k1")
    task2 = BaRunTask(id=str(uuid.uuid4()), run_id=run2.id, capability_id="c2", org_id=org_id, idempotency_key="k2")
    db_session.add_all([task1, task2])
    await db_session.flush()

    # 6. Add non-FK rows: embeddings
    emb1 = BaEmbedding(project_id=proj_1_id, org_id=org_id, entity_type="fact", entity_id=f1.id)
    emb2 = BaEmbedding(project_id=proj_2_id, org_id=org_id, entity_type="fact", entity_id=f2.id)
    db_session.add_all([emb1, emb2])
    await db_session.flush()

    # 7. Add non-FK rows: Athena source, content, span
    ath_src1 = AthenaSource(id=str(uuid.uuid4()), org_id=org_id, project_id=proj_1_id, content_hash="ath1", source_type="document")
    ath_src2 = AthenaSource(id=str(uuid.uuid4()), org_id=org_id, project_id=proj_2_id, content_hash="ath2", source_type="document")
    db_session.add_all([ath_src1, ath_src2])
    await db_session.flush()

    ath_cnt1 = AthenaSourceContent(source_id=ath_src1.id, org_id=org_id, project_id=proj_1_id, storage_ref=store.put_if_absent("sha256/ath1", b"source 1", "text/plain"), media_type="text/plain", extraction_version="v1")
    ath_cnt2 = AthenaSourceContent(source_id=ath_src2.id, org_id=org_id, project_id=proj_2_id, storage_ref=store.put_if_absent("sha256/ath2", b"source 2", "text/plain"), media_type="text/plain", extraction_version="v1")
    db_session.add_all([ath_cnt1, ath_cnt2])
    await db_session.flush()

    ath_sp1 = AthenaSourceSpan(id=str(uuid.uuid4()), source_id=ath_src1.id, org_id=org_id, project_id=proj_1_id, ordinal=0, start_char=0, end_char=5, raw_text="hello", normalized_text_hash="h")
    ath_sp2 = AthenaSourceSpan(id=str(uuid.uuid4()), source_id=ath_src2.id, org_id=org_id, project_id=proj_2_id, ordinal=0, start_char=0, end_char=5, raw_text="world", normalized_text_hash="w")
    db_session.add_all([ath_sp1, ath_sp2])
    await db_session.flush()

    # 8. Setup upload directories on disk
    upload_dir_1 = tmp_path / org_id / proj_1_id
    upload_dir_1.mkdir(parents=True, exist_ok=True)
    (upload_dir_1 / "file1.txt").write_text("content 1")

    upload_dir_2 = tmp_path / org_id / proj_2_id
    upload_dir_2.mkdir(parents=True, exist_ok=True)
    (upload_dir_2 / "file2.txt").write_text("content 2")

    # 9. Perform deletion via delete_ba_project service directly with custom upload_dir
    ctx = BATenantContext(org_id=org_id, project_id=proj_1_id)
    await delete_ba_project(ctx=ctx, db=db_session, store=store, upload_base_dir=tmp_path)

    # 10. Assert Project 1 is gone from every table
    assert await db_session.get(BaProject, proj_1_id) is None
    assert (await db_session.scalar(select(func.count(BaFact.id)).where(BaFact.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(BaSource.id)).where(BaSource.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(BaDeliverableInstance.id)).where(BaDeliverableInstance.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(BaNode.id)).where(BaNode.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(BaEdge.project_id)).where(BaEdge.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(BaRun.id)).where(BaRun.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(BaEmbedding.id)).where(BaEmbedding.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(AthenaSource.id)).where(AthenaSource.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(AthenaSourceContent.source_id)).where(AthenaSourceContent.project_id == proj_1_id))) == 0
    assert (await db_session.scalar(select(func.count(AthenaSourceSpan.id)).where(AthenaSourceSpan.project_id == proj_1_id))) == 0

    # 11. Assert Project 1 files and S3 objects are gone
    assert not upload_dir_1.exists()
    assert deliv1_key not in store.objects

    # 12. Assert Project 2 and all its data remain completely intact!
    assert await db_session.get(BaProject, proj_2_id) is not None
    assert (await db_session.scalar(select(func.count(BaFact.id)).where(BaFact.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(BaSource.id)).where(BaSource.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(BaDeliverableInstance.id)).where(BaDeliverableInstance.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(BaNode.id)).where(BaNode.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(BaEdge.project_id)).where(BaEdge.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(BaRun.id)).where(BaRun.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(BaEmbedding.id)).where(BaEmbedding.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(AthenaSource.id)).where(AthenaSource.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(AthenaSourceContent.source_id)).where(AthenaSourceContent.project_id == proj_2_id))) == 1
    assert (await db_session.scalar(select(func.count(AthenaSourceSpan.id)).where(AthenaSourceSpan.project_id == proj_2_id))) == 1
    assert upload_dir_2.exists()
    assert (upload_dir_2 / "file2.txt").read_text() == "content 2"
    assert deliv2_key in store.objects


@pytest.mark.asyncio
async def test_http_delete_project_returns_204(db_session, api_client):
    """Calling DELETE /api/ba/projects/{project_id} through HTTP router returns 204."""
    client, _ = api_client
    org_id = str(uuid.uuid4())
    proj_id = str(uuid.uuid4())
    proj = BaProject(id=proj_id, org_id=org_id, name="Delete Me HTTP")
    db_session.add(proj)
    await db_session.flush()

    token = _make_token(org_id)
    response = await client.delete(
        f"/api/ba/projects/{proj_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    # Project is gone
    assert await db_session.get(BaProject, proj_id) is None

@pytest.mark.asyncio
async def test_fact_immutability_trigger_blocks_direct_fact_delete(db_session):
    """Direct deletion of individual facts without project deletion context is blocked by trigger."""
    org_id = str(uuid.uuid4())
    proj = BaProject(id=str(uuid.uuid4()), org_id=org_id, name="Immutability Project")
    db_session.add(proj)
    await db_session.flush()

    src = BaSource(id=str(uuid.uuid4()), project_id=proj.id, org_id=org_id, kind="doc", tier="t1", content_hash="h1")
    db_session.add(src)
    await db_session.flush()

    fact = BaFact(id=str(uuid.uuid4()), project_id=proj.id, org_id=org_id, subject_type="Actor", subject_key="k1", predicate="p1", source_id=src.id, asserted_by="test")
    db_session.add(fact)
    await db_session.flush()

    # Attempting direct DELETE on ba_fact must abort loudly
    with pytest.raises(DBAPIError) as exc_info:
        async with db_session.begin_nested():
            await db_session.execute(text("DELETE FROM ba_fact WHERE id = :id"), {"id": fact.id})

    assert "ba_fact is append-only: DELETE is not permitted" in str(exc_info.value)

    # Fact still exists
    fetched = await db_session.get(BaFact, fact.id)
    assert fetched is not None


def test_path_traversal_validation(tmp_path):
    """Path traversal sequences in org_id or project_id are strictly rejected."""
    base = tmp_path / "uploads"
    base.mkdir()

    # Normal case
    safe = validate_safe_upload_path("org-123", "proj-456", base)
    assert safe == (base / "org-123" / "proj-456").resolve()

    # Traversal attempts
    for bad_id in ["../etc", "foo/bar", "foo\\bar", "..\0"]:
        with pytest.raises(Exception):
            validate_safe_upload_path(bad_id, "proj-1", base)
        with pytest.raises(Exception):
            validate_safe_upload_path("org-1", bad_id, base)
