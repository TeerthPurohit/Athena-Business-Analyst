"""Tenant-scoped persistence for Athena source evidence."""

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athena.ingestion import ingest_text_source
from athena.models import AthenaSource, AthenaSourceContent, AthenaSourceSpan
from athena.storage import ObjectStore


@dataclass(frozen=True)
class IngestedSource:
    source: AthenaSource
    spans: tuple[AthenaSourceSpan, ...]
    created: bool


class AthenaRepository:
    """All source lookups retain tenant scope in their SQL statement."""

    def __init__(self, session: AsyncSession, store: ObjectStore):
        self._session = session
        self._store = store

    async def ingest(
        self,
        *,
        org_id: str,
        project_id: str,
        filename: str,
        content: bytes,
        media_type: str,
    ) -> IngestedSource:
        content_hash = hashlib.sha256(content).hexdigest()
        source = await self._session.scalar(
            select(AthenaSource).where(
                AthenaSource.org_id == org_id,
                AthenaSource.project_id == project_id,
                AthenaSource.content_hash == content_hash,
            )
        )
        if source is not None:
            spans = tuple(
                (
                    await self._session.scalars(
                        select(AthenaSourceSpan)
                        .where(
                            AthenaSourceSpan.org_id == org_id,
                            AthenaSourceSpan.project_id == project_id,
                            AthenaSourceSpan.source_id == source.id,
                        )
                        .order_by(AthenaSourceSpan.ordinal)
                    )
                ).all()
            )
            return IngestedSource(source=source, spans=spans, created=False)

        draft = ingest_text_source(
            org_id=org_id,
            project_id=project_id,
            filename=filename,
            content=content,
            media_type=media_type,
            store=self._store,
        )
        source = AthenaSource(
            org_id=org_id,
            project_id=project_id,
            content_hash=draft.content_hash,
            source_type="document",
        )
        self._session.add(source)
        await self._session.flush()
        self._session.add(
            AthenaSourceContent(
                source_id=source.id,
                org_id=org_id,
                project_id=project_id,
                storage_ref=draft.storage_ref,
                media_type=media_type,
                extraction_version="athena-text-v1",
            )
        )
        spans = tuple(
            AthenaSourceSpan(
                source_id=source.id,
                org_id=org_id,
                project_id=project_id,
                ordinal=span.ordinal,
                start_char=span.start_char,
                end_char=span.end_char,
                raw_text=span.raw_text,
                normalized_text_hash=span.normalized_text_hash,
            )
            for span in draft.spans
        )
        self._session.add_all(spans)
        await self._session.flush()
        return IngestedSource(source=source, spans=spans, created=True)
