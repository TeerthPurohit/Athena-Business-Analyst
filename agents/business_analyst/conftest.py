import os

# Test runs must never send traces to the real Langfuse project (see observability.py).
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
# ...nor append runs to the real cost log (see cost_log.py).
os.environ.setdefault("BA_COST_LOG_PATH", "")
import uuid

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.models import BaProject
from models.engine import engine

ROOT = Path(__file__).resolve().parents[2]

# There is no separate test database: DATABASE_URL (from the shell, else .env) is whatever these
# tests read, write, and migrate — and .env points at a live Neon DB. DB tests therefore run only
# when a human has confirmed DATABASE_URL is a disposable branch.
DB_CONFIRMED = os.environ.get("BA_TEST_DB_CONFIRMED") == "1"
DB_SKIP_REASON = (
    "DB test skipped: set BA_TEST_DB_CONFIRMED=1 only after confirming DATABASE_URL points at a "
    "disposable database/branch; these tests write to (and migrate) whatever DATABASE_URL names."
)


@event.listens_for(Engine, "do_connect")
def _refuse_unconfirmed_postgres(dialect, conn_rec, cargs, cparams):
    """Backstop for tests that use models.engine.engine (or any Postgres engine) directly instead
    of the db_session fixture: skip at the moment a real connection would be opened, before any
    bytes reach the database. pytest.skip raises a BaseException, so app-level `except Exception`
    handlers can't swallow it. In-memory SQLite engines are unaffected."""
    if not DB_CONFIRMED and dialect.name == "postgresql":
        pytest.skip(DB_SKIP_REASON)


@pytest.fixture(scope="session", autouse=True)
def _ba_schema_at_head():
    """Runs once per test session, before the first test in this directory that needs it —
    guarantees the ba_* schema exists regardless of which test file pytest collects first.
    No-op without BA_TEST_DB_CONFIRMED: this fixture is autouse, so skipping here would skip
    every BA test, including the pure ones; DB tests skip individually instead."""
    if DB_CONFIRMED:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


@pytest_asyncio.fixture
async def db_session():
    """An AsyncSession bound to a single connection's transaction, rolled back after the test.
    Because ba_fact blocks DELETE/TRUNCATE, this is the only practical way to clean up test
    data — the transaction is simply never committed."""
    if not DB_CONFIRMED:
        pytest.skip(DB_SKIP_REASON)
    async with engine.connect() as connection:
        async with connection.begin() as outer_txn:
            session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
            try:
                yield session
            finally:
                await session.close()
                await outer_txn.rollback()
    # dispose() after every use — see test_migrations.py's _table_exists() comment: pytest-asyncio
    # gives each test its own event loop, but models.engine.engine's pool is process-lifetime.
    # Without disposing, the next test's loop can be handed a connection bound to this test's
    # now-closed loop, raising "Event loop is closed".
    await engine.dispose()


@pytest_asyncio.fixture
async def ba_project(db_session):
    # org_id is String(36) (models.py) — a bare uuid4() str is exactly 36 chars; an "org-"
    # prefix would overflow it and raise StringDataRightTruncationError against real Postgres.
    project = BaProject(id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), name="Test Project")
    db_session.add(project)
    await db_session.flush()
    return project
