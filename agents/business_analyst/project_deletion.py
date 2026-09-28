"""Tenant-scoped deletion of a BA project and its stored artifacts."""

import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.models import BaDeliverableInstance, BaProject
from athena.models import AthenaSourceContent
from athena.storage import ObjectStore

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "ba_uploads"


def validate_safe_upload_path(org_id: str, project_id: str, base_dir: Path) -> Path:
    """Keep a project upload directory inside its configured root."""
    for value in (org_id, project_id):
        if not value or value in {".", ".."} or ".." in value or any(c in value for c in "/\\\0"):
            raise HTTPException(status_code=400, detail="Invalid project path.")
    base = base_dir.resolve()
    target = (base / org_id / project_id).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid project path.")
    return target


async def delete_ba_project(
    ctx: BATenantContext,
    db: AsyncSession,
    store: ObjectStore | None = None,
    upload_base_dir: Path | None = None,
) -> None:
    """Delete one tenant's project; never permit ordinary fact mutation."""
    org_id, project_id = str(ctx.org_id), str(ctx.project_id)
    upload_dir = validate_safe_upload_path(org_id, project_id, upload_base_dir or UPLOAD_DIR)
    project = (await db.scalars(
        select(BaProject).where(BaProject.id == project_id, BaProject.org_id == org_id).with_for_update()
    )).one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    prefix = f"deliverables/{org_id}/{project_id}/"
    deliverables = (await db.scalars(select(BaDeliverableInstance).where(
        BaDeliverableInstance.project_id == project_id, BaDeliverableInstance.org_id == org_id,
    ))).all()
    deliverable_refs = []
    for instance in deliverables:
        ref = instance.content_ref
        if not ref.startswith("s3://"):
            continue  # Legacy inline content has no external artifact.
        if not urlparse(ref).path.lstrip("/").startswith(prefix):
            raise HTTPException(status_code=409, detail="Project contains an unexpected deliverable reference.")
        deliverable_refs.append(ref)

    contents = (await db.scalars(select(AthenaSourceContent).where(
        AthenaSourceContent.project_id == project_id, AthenaSourceContent.org_id == org_id,
    ))).all()
    source_refs = set()
    for content in contents:
        ref = content.storage_ref
        if not ref.startswith("s3://"):
            continue
        shared = await db.scalar(select(func.count(AthenaSourceContent.source_id)).where(
            AthenaSourceContent.storage_ref == ref,
            or_(AthenaSourceContent.project_id != project_id, AthenaSourceContent.org_id != org_id),
        ))
        if not shared:
            source_refs.add(ref)

    if store is None and (deliverable_refs or source_refs):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project storage is unavailable. Nothing was deleted; try again later.",
        )

    params = {"project_id": project_id, "org_id": org_id}
    try:
        # Ordinary DELETE remains blocked by the ba_fact trigger; this transaction
        # grants deletion only to facts belonging to this one project.
        await db.execute(text("SELECT set_config('athena.allow_project_deletion', 'true', true)"))
        await db.execute(text("SELECT set_config('athena.deleting_project_id', :project_id, true)"), params)
        for statement in (
            "DELETE FROM ba_embeddings WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM athena_source_span WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM athena_source_content WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM athena_source WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM ba_deliverable_instance WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM ba_edge WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM ba_node WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM ba_run_task WHERE run_id IN (SELECT id FROM ba_run WHERE project_id = :project_id AND org_id = :org_id)",
            "DELETE FROM ba_run WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM ba_fact WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM ba_source WHERE project_id = :project_id AND org_id = :org_id",
            "DELETE FROM ba_project WHERE id = :project_id AND org_id = :org_id",
        ):
            await db.execute(text(statement), params)

        # Perform irreversible cleanup only after the database has accepted all
        # deletes. Storage is not transactionally coupled to PostgreSQL, so a
        # partial storage failure can still leave missing blobs; never claim 204.
        if store is not None:
            for ref in deliverable_refs:
                await asyncio.to_thread(store.delete, ref)
            await asyncio.to_thread(store.delete_prefix, prefix)
            for ref in source_refs:
                await asyncio.to_thread(store.delete, ref)
        if upload_dir.is_dir():
            await asyncio.to_thread(shutil.rmtree, upload_dir)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Project deletion failed. The project remains available; contact support if stored files are missing.",
        ) from exc
