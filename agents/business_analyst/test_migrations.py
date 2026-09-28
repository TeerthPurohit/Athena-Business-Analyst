"""Real-Postgres migration tests. Run from repo root: pytest agents/business_analyst/test_migrations.py -v

DESTRUCTIVE: runs `alembic downgrade base`, dropping every ba_* table and its data in whatever
DATABASE_URL names. Requires BOTH BA_TEST_DB_CONFIRMED=1 and BA_ALLOW_DESTRUCTIVE_MIGRATION_TEST=1."""
import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from models.engine import engine

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("BA_TEST_DB_CONFIRMED") != "1",
        reason="DB test skipped: set BA_TEST_DB_CONFIRMED=1 only after confirming DATABASE_URL "
        "points at a disposable database/branch.",
    ),
    pytest.mark.skipif(
        os.environ.get("BA_ALLOW_DESTRUCTIVE_MIGRATION_TEST") != "1",
        reason="Destructive migration test skipped: it runs `alembic downgrade base` (drops all "
        "ba_* tables and data). Set BA_ALLOW_DESTRUCTIVE_MIGRATION_TEST=1 only on a throwaway DB.",
    ),
]


def _config() -> Config:
    return Config(str(ALEMBIC_INI))


async def _table_exists(name: str) -> bool:
    # engine.dispose() after every use: this function is called from a fresh asyncio.run() each
    # time (see test below), and models.engine.engine's pool is process-lifetime — a pooled
    # asyncpg connection checked out in one asyncio.run() call is bound to that call's event
    # loop, which is closed by the time the next asyncio.run() call reuses the pool, raising
    # "Event loop is closed". Disposing forces a fresh connection on the next checkout.
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :name"),
            {"name": name},
        )
        exists = result.first() is not None
    await engine.dispose()
    return exists


def test_upgrade_head_creates_ba_tables_then_restores_head():
    cfg = _config()
    command.upgrade(cfg, "head")
    try:
        for table in ("ba_project", "ba_source", "ba_fact", "ba_node", "ba_edge", "ba_capability", "ba_ontology_type"):
            assert asyncio.run(_table_exists(table)), f"{table} should exist after upgrade head"

        command.downgrade(cfg, "base")
        for table in ("ba_project", "ba_source", "ba_fact", "ba_node", "ba_edge", "ba_capability", "ba_ontology_type"):
            assert not asyncio.run(_table_exists(table)), f"{table} should be gone after downgrade base"
    finally:
        # Leave the schema at head regardless of outcome — later test modules assume it's there.
        command.upgrade(cfg, "head")
        assert asyncio.run(_table_exists("ba_fact"))
