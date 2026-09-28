# agents/business_analyst/test_fact_immutability.py
"""Confirms the ba_0002 triggers (Task 3) actually block writes on real rows.
Structural trigger-existence checks live in test_trigger_migration.py; this file is behavioral.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agents.business_analyst.facts import BATenantContext, assert_fact, register_source


async def _make_fact(db_session, ba_project, subject_key: str):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(
        ctx, db_session, kind="document", tier="tier_1", content_hash=f"hash-{subject_key}"
    )
    return await assert_fact(
        ctx,
        db_session,
        subject_type="Actor",
        subject_key=subject_key,
        predicate="p1",
        value={"v": 1},
        source_id=source.id,
        asserted_by="test-suite",
    )


@pytest.mark.asyncio
async def test_update_ba_fact_raises(db_session, ba_project):
    fact = await _make_fact(db_session, ba_project, "immutable-update")

    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE ba_fact SET predicate = 'hacked' WHERE id = :id"), {"id": fact.id}
            )

    # Follow-up query on the still-live outer transaction: the row must be untouched,
    # proving the trigger actually blocked the write rather than some unrelated error firing.
    result = await db_session.execute(
        text("SELECT predicate FROM ba_fact WHERE id = :id"), {"id": fact.id}
    )
    assert result.scalar_one() == "p1"


@pytest.mark.asyncio
async def test_delete_ba_fact_raises(db_session, ba_project):
    fact = await _make_fact(db_session, ba_project, "immutable-delete")

    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text("DELETE FROM ba_fact WHERE id = :id"), {"id": fact.id})

    # Row must still exist — the DELETE never took effect.
    result = await db_session.execute(
        text("SELECT count(*) FROM ba_fact WHERE id = :id"), {"id": fact.id}
    )
    assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_truncate_ba_fact_raises(db_session, ba_project):
    fact = await _make_fact(db_session, ba_project, "immutable-truncate")

    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text("TRUNCATE ba_fact"))

    # Table must still contain the row — TRUNCATE never took effect.
    result = await db_session.execute(
        text("SELECT count(*) FROM ba_fact WHERE id = :id"), {"id": fact.id}
    )
    assert result.scalar_one() == 1
