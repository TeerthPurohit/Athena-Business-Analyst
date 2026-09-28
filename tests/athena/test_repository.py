import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from athena.models import AthenaSource, AthenaSourceSpan, Base


class FakeObjectStore:
    def __init__(self):
        self.writes = 0

    def put_if_absent(self, key, content, media_type):
        self.writes += 1
        return f"memory://{key}"


@pytest.mark.asyncio
async def test_repository_deduplicates_content_within_tenant_project():
    from athena.repository import AthenaRepository

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = FakeObjectStore()
    async with sessions() as session:
        repository = AthenaRepository(session, store)
        first = await repository.ingest(
            org_id="org-a",
            project_id="project-a",
            filename="meeting.txt",
            content=b"Manager approves stock adjustments.",
            media_type="text/plain",
        )
        second = await repository.ingest(
            org_id="org-a",
            project_id="project-a",
            filename="copy.txt",
            content=b"Manager approves stock adjustments.",
            media_type="text/plain",
        )
        await session.commit()

        assert first.created is True
        assert second.created is False
        assert second.source.id == first.source.id
        assert store.writes == 1
        assert len((await session.scalars(select(AthenaSource))).all()) == 1
        assert len((await session.scalars(select(AthenaSourceSpan))).all()) == 1

    await engine.dispose()
