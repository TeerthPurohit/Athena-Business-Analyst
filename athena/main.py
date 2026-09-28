"""Configured Athena application factory for ASGI deployment."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from athena.api import create_app
from athena.config import Settings
from athena.db import create_session_factory
from athena.repository import AthenaRepository, IngestedSource
from athena.storage import ObjectStore, S3ObjectStore


class SessionScopedRepository:
    """Commits one source-ingestion transaction per HTTP request."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], store: ObjectStore):
        self._sessions = sessions
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
        async with self._sessions() as session:
            try:
                result = await AthenaRepository(session, self._store).ingest(
                    org_id=org_id,
                    project_id=project_id,
                    filename=filename,
                    content=content,
                    media_type=media_type,
                )
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


def create_configured_app() -> object:
    """Build an Athena app only when database and object storage are configured."""
    settings = Settings()
    required_storage = (
        settings.object_storage_endpoint,
        settings.object_storage_access_key,
        settings.object_storage_secret_key,
        settings.object_storage_bucket,
        settings.object_storage_region,
    )
    if not all(required_storage):
        raise ValueError("Neon object storage must be configured for source ingestion")

    sessions = create_session_factory(settings.require_database())
    store = S3ObjectStore(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        bucket=settings.object_storage_bucket,
        region=settings.object_storage_region,
        force_path_style=settings.object_storage_force_path_style,
    )
    repository = SessionScopedRepository(sessions, store)
    return create_app(repository_factory=lambda: repository)
