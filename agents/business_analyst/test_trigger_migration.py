"""Verify the BA fact protection triggers installed by migration."""

import pytest
from sqlalchemy import text

from models.engine import engine


@pytest.mark.asyncio
async def test_fact_protection_triggers_installed():
    async with engine.connect() as connection:
        names = set((await connection.execute(text(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'ba_fact'::regclass "
            "AND NOT tgisinternal"
        ))).scalars())
    await engine.dispose()
    assert {"ba_fact_no_update_delete", "ba_fact_no_truncate"} <= names
