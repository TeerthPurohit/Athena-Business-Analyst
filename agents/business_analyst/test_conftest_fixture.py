import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_db_session_sees_its_own_write(db_session):
    await db_session.execute(
        text(
            "INSERT INTO ba_project (id, org_id, name, created_at) "
            "VALUES (:id, :org_id, :name, now())"
        ),
        {"id": "test-rollback-project", "org_id": "org-test", "name": "rollback check"},
    )
    result = await db_session.execute(
        text("SELECT 1 FROM ba_project WHERE id = :id"), {"id": "test-rollback-project"}
    )
    assert result.first() is not None


@pytest.mark.asyncio
async def test_previous_tests_write_did_not_leak_into_this_one(db_session):
    # Runs after the test above in file order. If db_session didn't roll back, this row
    # would still be visible here — proving the fixture actually isolates tests.
    result = await db_session.execute(
        text("SELECT 1 FROM ba_project WHERE id = :id"), {"id": "test-rollback-project"}
    )
    assert result.first() is None


@pytest.mark.asyncio
async def test_ba_project_fixture_gives_a_flushed_row(db_session, ba_project):
    result = await db_session.execute(
        text("SELECT org_id FROM ba_project WHERE id = :id"), {"id": ba_project.id}
    )
    row = result.first()
    assert row is not None
    assert row[0] == ba_project.org_id
