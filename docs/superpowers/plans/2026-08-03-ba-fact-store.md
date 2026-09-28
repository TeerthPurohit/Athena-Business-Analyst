# BA OS Phase 1: Fact Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the append-only fact store for the Business Analyst OS — `ba_project`,
`ba_source`, `ba_fact` tables, the immutability trigger, and the `facts.py` repo functions —
so every later BA OS phase (graph projection, planner, quality engine, ...) has a real,
tested foundation to read from and write to.

**Architecture:** SQLAlchemy 2.0 declarative models with their own `Base` (deliberately not the
shared `models.base.Base`), created via a new root-level Alembic setup scoped to `ba_*` tables
only. Facts are asserted, never updated — corrections and approvals are new rows linked backward
via a `replaces` pointer. Two Postgres triggers make `UPDATE`/`DELETE`/`TRUNCATE` on `ba_fact`
fail loudly. All repo functions in `facts.py` take a `BATenantContext` first and filter by
`org_id` in the same query as everything else.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 (async, asyncpg), Alembic, Postgres (real DB for
tests — the trigger cannot be tested against sqlite), pytest + pytest-asyncio.

**Design doc:** [docs/superpowers/specs/2026-08-03-ba-fact-store-design.md](../specs/2026-08-03-ba-fact-store-design.md)
— read it before starting; this plan implements it exactly, including its two corrections
(`replaces` instead of `retracted_by`, `approve_fact` as an insert not an update) and its
"Conventions adopted" section (separate `Base`, `String(36)` ids, no local FK on `org_id`, flat
files for this phase).

## Progress

| # | Task | Status | Commits | Review |
|---|------|--------|---------|--------|
| 1 | SQLAlchemy models (`models.py`) | ✅ Done | `3bd3e6e..4abecf1` | Clean |
| 2 | Alembic setup + initial migration | ✅ Done | `4abecf1..64d9c3f` (+ `f4a16bb` doc fix) | Clean (1 minor, deferred) |
| 3 | Immutability triggers migration | ✅ Done | `f4a16bb..f19786a` | Clean |
| 4 | Test DB fixtures (`conftest.py`) | ✅ Done | `b1b7f56` | Clean (1 minor, deferred) |
| 5 | `facts.py` — `BATenantContext`, `register_source`, `assert_fact` | ✅ Done | `9bd7f85` | Clean (1 minor, deferred) |
| 6 | `facts.py` — `get_facts` | ✅ Done | `2d8abb5` | Clean (1 minor, deferred) |
| 7 | `facts.py` — `approve_fact` | ✅ Done | `ca4d6b2`, `406fffe` | 1 fix round (identity-map test bug), clean after |
| 8 | Behavioral immutability tests | ✅ Done | `79ee639`, `975ef21` (merged via `ecc3045`) | Clean (1 fix: checklist ticks) |
| — | Final check: full BA suite + root regression suite | ✅ Done | — | BA 23/23; root 373/380 (7 pre-existing, unrelated, confirmed) |
| — | Final whole-branch code review | ✅ Done | `6b6e8ac` (fix round) | 3 Important + 3 Minor found, all fixed, re-review clean |

Update this table as each task completes — it's the fast-glance status; the SDD ledger
(`.superpowers/sdd/2026-08-03-ba-fact-store/progress.md`) has the full per-task detail (minor
findings, blockers hit, fixes applied).

## Global Constraints

- IDs are `String(36)` UUID strings (`str(uuid.uuid4())`), matching root convention
  (`models/plan.py`, `models/agent.py`) — never native `PG_UUID`.
- `org_id` is an opaque `String(36)` column with **no local foreign key** — organizations are
  owned by Node/Prisma, Python never joins against them locally.
- `ba_fact.seq` (`BigInteger`, `Identity(always=True)`) is the **only** ordering tiebreaker for
  facts. Never use `asserted_at` or any wall-clock column to order or break ties.
- Alembic revision ids must be **≤32 characters** — `alembic_version.version_num` (and BA's own
  scoped `ba_alembic_version.version_num`) is a hardcoded `VARCHAR(32)` in Alembic itself, and a
  longer id fails at `upgrade` time with `StringDataRightTruncationError`, not at authoring time.
  `ba_0001_fact_store_tables` (25 chars) and `ba_0002_fact_triggers` (21 chars) both fit.
- `ba_fact` has **no `retracted_by` column**. Corrections/approvals are new rows with `replaces`
  pointing backward at the fact they supersede, set once at insert time. Nothing is ever written
  to an existing `ba_fact` row after it's inserted.
- `human_approval` is a privileged field. `assert_fact`'s public signature must never accept a
  `human_approval` parameter — only `approve_fact` may produce a fact with `human_approval=True`,
  and it does so via a `replaces`-linked insert, never an update.
- BA's SQLAlchemy models use their **own** `Base` (`agents/business_analyst/models.py`), not
  `models.base.Base`. Alembic's `env.py` must only ever import
  `agents.business_analyst.models.Base.metadata` as `target_metadata`.
- Every function in `facts.py` takes `ctx: BATenantContext` as its first parameter and filters by
  `ctx.org_id` in the same query as every other `WHERE` clause — never filter tenant scope in
  Python after an unscoped query.
- `agents/business_analyst/models.py` and `facts.py` stay single, flat files for this phase (see
  design doc "File-growth thresholds" for the concrete split triggers — not relevant to writing
  this plan, only to whoever revisits it later).
- Every test that touches the database is `@pytest.mark.asyncio` and runs against real Postgres
  (`DATABASE_URL` from `.env`, via `models.engine.engine`) — this repo's existing async test
  convention (see `agents/universal-agent/agents_scrapper/sub_agents/test_base.py` and siblings).
- `models.engine.engine`'s connection pool is process-lifetime. Any code path that makes more
  than one independent top-level `asyncio.run()` call against it in the same process (Tasks 2
  and 3's migration tests do this, mixing Alembic's own internal `asyncio.run()` inside
  `command.upgrade()`/`downgrade()` with a caller's separate checks) must call
  `await engine.dispose()` at the end of each `asyncio.run()`-wrapped unit of work — a pooled
  asyncpg connection is bound to the event loop that created it, and reusing it after that loop
  closes raises `RuntimeError: Event loop is closed`. Tests that stay inside a single
  `@pytest.mark.asyncio` test function (one event loop for the whole test, per pytest-asyncio)
  never hit this — it is specific to code mixing `asyncio.run()` calls at the top level.

---

### Task 1: SQLAlchemy models

**Files:**
- Create: `agents/business_analyst/models.py`
- Test: `agents/business_analyst/test_models.py`

**Interfaces:**
- Produces: `agents.business_analyst.models.Base` (BA's own `DeclarativeBase`, distinct from
  `models.base.Base`), `BaProject`, `BaSource`, `BaFact` — all later tasks import from here.

- [ ] **Step 1: Write the failing test**

```python
# agents/business_analyst/test_models.py
from models.base import Base as RootBase

from agents.business_analyst.models import Base, BaFact, BaProject, BaSource


def test_ba_uses_its_own_base_not_the_shared_root_base():
    assert Base is not RootBase
    # The whole point of the separate Base: Alembic's autogenerate (scoped to Base.metadata)
    # must never be able to see a root table and emit a spurious CREATE TABLE for it.
    assert "ba_fact" not in RootBase.metadata.tables


def test_table_names():
    assert BaProject.__tablename__ == "ba_project"
    assert BaSource.__tablename__ == "ba_source"
    assert BaFact.__tablename__ == "ba_fact"


def test_ba_fact_columns():
    cols = set(BaFact.__table__.columns.keys())
    assert cols == {
        "id", "project_id", "org_id", "seq", "subject_type", "subject_key",
        "predicate", "value", "object_type", "object_key", "source_id",
        "run_id", "human_approval", "asserted_at", "asserted_by", "replaces",
    }
    # no retracted_by — see design doc's "Retraction is a forward-only pointer" correction
    assert "retracted_by" not in cols


def test_ba_fact_has_no_updated_at_style_column():
    # seq is the only ordering tiebreaker; asserted_at is display-only and there is no
    # updated_at at all, because rows are never updated.
    assert "updated_at" not in BaFact.__table__.columns.keys()


def test_ba_fact_seq_is_identity_and_not_the_primary_key():
    seq_col = BaFact.__table__.columns["seq"]
    assert seq_col.identity is not None
    assert seq_col.primary_key is False


def test_ba_fact_human_approval_defaults_false():
    col = BaFact.__table__.columns["human_approval"]
    assert col.nullable is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/business_analyst/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.business_analyst.models'`

- [ ] **Step 3: Write the implementation**

```python
# agents/business_analyst/models.py
"""SQLAlchemy models for the BA OS append-only fact store.

See docs/superpowers/specs/2026-08-03-ba-fact-store-design.md for the design rationale,
including why this uses its own Base instead of the shared models.base.Base, and why there
is a `replaces` column instead of the `retracted_by` the architecture plan originally proposed.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Identity, Index, String, BigInteger, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """BA's own declarative base — deliberately NOT models.base.Base. Alembic's env.py points
    target_metadata at this Base only, so autogenerate can never see (and can never emit a
    spurious CREATE TABLE for) any non-ba_* table, even if one is added later without a
    matching Alembic revision. Root's other tables keep being created via create_all(),
    untouched by this Alembic setup."""


class BaProject(Base):
    __tablename__ = "ba_project"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class BaSource(Base):
    __tablename__ = "ba_source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ba_project.id"), nullable=False, index=True
    )
    # Denormalized alongside project_id so tenant checks never require a join to enforce —
    # the S3 lesson from architecture_plan.md §0: filter in the same statement as ORDER BY/LIMIT.
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    tier: Mapped[str] = mapped_column(String(50), nullable=False)
    ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    stakeholder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class BaFact(Base):
    __tablename__ = "ba_fact"
    __table_args__ = (
        Index("ix_ba_fact_project_subject", "project_id", "subject_type", "subject_key"),
        Index("ix_ba_fact_project_seq", "project_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ba_project.id"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # GENERATED ALWAYS AS IDENTITY — the ONLY ordering tiebreaker for facts. Never asserted_at:
    # wall-clock ties and skew would make projection non-deterministic.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    subject_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    predicate: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict | None] = mapped_column(JSONB(), nullable=True)
    object_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("ba_source.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Privileged field — see Global Constraints. Only agents.business_analyst.facts.approve_fact
    # may produce a fact with this True, via a replaces-linked insert.
    human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    asserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    asserted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    # Forward-only pointer at the fact this one supersedes, set once at insert time — never
    # retracted_by. See design doc's "Retraction is a forward-only pointer" correction.
    replaces: Mapped[str | None] = mapped_column(String(36), ForeignKey("ba_fact.id"), nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/business_analyst/test_models.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/business_analyst/models.py agents/business_analyst/test_models.py
git commit -m "feat(ba): add fact store SQLAlchemy models (ba_project/ba_source/ba_fact)"
```

---

### Task 2: Alembic setup + initial migration (tables, no triggers yet)

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/ba_0001_fact_store_tables.py`
- Test: `agents/business_analyst/test_migrations.py`

**Interfaces:**
- Consumes: `agents.business_analyst.models.Base` (Task 1), `models.engine.engine` (existing —
  root's already-configured async engine with SSL/pool args baked in).
- Produces: a working `alembic upgrade head` / `alembic downgrade` CLI at repo root, and the
  `ba_project`/`ba_source`/`ba_fact` tables in the real database once run.

- [ ] **Step 1: Write the failing test**

```python
# agents/business_analyst/test_migrations.py
"""Real-Postgres migration tests. Run from repo root: pytest agents/business_analyst/test_migrations.py -v"""
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from models.engine import engine

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"


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
        for table in ("ba_project", "ba_source", "ba_fact"):
            assert asyncio.run(_table_exists(table)), f"{table} should exist after upgrade head"

        command.downgrade(cfg, "base")
        for table in ("ba_project", "ba_source", "ba_fact"):
            assert not asyncio.run(_table_exists(table)), f"{table} should be gone after downgrade base"
    finally:
        # Leave the schema at head regardless of outcome — later test modules assume it's there.
        command.upgrade(cfg, "head")
        assert asyncio.run(_table_exists("ba_fact"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/business_analyst/test_migrations.py -v`
Expected: FAIL — `alembic.ini` doesn't exist yet (`FileNotFoundError` or Alembic's own
"path doesn't exist" error).

- [ ] **Step 3: Write the implementation**

Create `alembic.ini` (root):

```ini
# A generic, single database configuration.

[alembic]
# path to migration scripts.
script_location = %(here)s/alembic

# sys.path path, will be prepended to sys.path if present.
prepend_sys_path = .

path_separator = os

# database URL. Unused for online mode (env.py builds the engine from models.engine.engine
# directly, so it inherits the app's SSL/pool config); kept here only so offline mode has a
# non-empty placeholder.
sqlalchemy.url = driver://user:pass@localhost/dbname


[post_write_hooks]

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

Create `alembic/script.py.mako` (verbatim Alembic default template, matching the one already
used by `agents/universal-agent/alembic/script.py.mako`):

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades if downgrades else "pass"}
```

Create `alembic/env.py`:

```python
import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Scoped to BA's own Base only — see agents/business_analyst/models.py's docstring for why
# this must never be the shared models.base.Base.
from agents.business_analyst.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="ba_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # version_table is scoped, not the default "alembic_version" — root's DATABASE_URL and
    # agents/universal-agent's point at the same physical database, which already runs its own
    # Alembic history against the default table name. Sharing it would corrupt that history.
    context.configure(
        connection=connection, target_metadata=target_metadata, version_table="ba_alembic_version"
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Reuse the app's already-configured engine (SSL context, PgBouncer-safe statement cache
    # settings) instead of rebuilding connection plumbing here.
    from models.engine import engine

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    # Alembic's command.upgrade()/downgrade() each call asyncio.run() independently, closing
    # their event loop on return. models.engine.engine's pool is process-lifetime, so without
    # disposing here, a connection this loop checked out gets handed to the NEXT asyncio.run()
    # call (whether another Alembic command or a caller's own DB check) already bound to a dead
    # loop, raising "Event loop is closed". Disposing forces a fresh connection next checkout.
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `alembic/versions/ba_0001_fact_store_tables.py`:

```python
"""ba_project, ba_source, ba_fact tables (no triggers yet — see ba_0002).

Revision ID: ba_0001_fact_store_tables
Revises:
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "ba_0001_fact_store_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ba_project",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ba_project_org_id", "ba_project", ["org_id"])

    op.create_table(
        "ba_source",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("ba_project.id"), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("tier", sa.String(50), nullable=False),
        sa.Column("ref", sa.Text(), nullable=True),
        sa.Column("stakeholder_id", sa.String(36), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_ba_source_project_id", "ba_source", ["project_id"])
    op.create_index("ix_ba_source_org_id", "ba_source", ["org_id"])

    op.create_table(
        "ba_fact",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("ba_project.id"), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False, unique=True),
        sa.Column("subject_type", sa.String(100), nullable=False),
        sa.Column("subject_key", sa.String(255), nullable=False),
        sa.Column("predicate", sa.String(100), nullable=False),
        sa.Column("value", JSONB(), nullable=True),
        sa.Column("object_type", sa.String(100), nullable=True),
        sa.Column("object_key", sa.String(255), nullable=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("ba_source.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("human_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asserted_by", sa.String(255), nullable=False),
        sa.Column("replaces", sa.String(36), sa.ForeignKey("ba_fact.id"), nullable=True),
    )
    op.create_index("ix_ba_fact_project_id", "ba_fact", ["project_id"])
    op.create_index("ix_ba_fact_org_id", "ba_fact", ["org_id"])
    op.create_index(
        "ix_ba_fact_project_subject", "ba_fact", ["project_id", "subject_type", "subject_key"]
    )
    op.create_index("ix_ba_fact_project_seq", "ba_fact", ["project_id", "seq"])


def downgrade() -> None:
    op.drop_table("ba_fact")
    op.drop_table("ba_source")
    op.drop_table("ba_project")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/business_analyst/test_migrations.py -v`
Expected: PASS. Confirm manually too: `alembic current` should print `ba_0001_fact_store_tables (head)`.

- [ ] **Step 5: Commit**

```bash
git add alembic.ini alembic/env.py alembic/script.py.mako alembic/versions/ba_0001_fact_store_tables.py agents/business_analyst/test_migrations.py
git commit -m "feat(ba): add root Alembic setup scoped to ba_* tables, initial fact store migration"
```

---

### Task 3: Immutability triggers migration

**Files:**
- Create: `alembic/versions/ba_0002_fact_triggers.py`
- Test: `agents/business_analyst/test_trigger_migration.py`

**Interfaces:**
- Consumes: `ba_0001_fact_store_tables` (Task 2) as `down_revision`.
- Produces: two Postgres triggers (`ba_fact_no_update_delete`, `ba_fact_no_truncate`) on
  `ba_fact`. Task 8 tests their actual blocking behavior; this task only tests that they exist
  after `upgrade` and don't after `downgrade`.

- [ ] **Step 1: Write the failing test**

```python
# agents/business_analyst/test_trigger_migration.py
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from models.engine import engine

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"

TRIGGER_NAMES = {"ba_fact_no_update_delete", "ba_fact_no_truncate"}


def _config() -> Config:
    return Config(str(ALEMBIC_INI))


async def _trigger_names_on_ba_fact() -> set[str]:
    # dispose() after every use — see Task 2's identical _table_exists() comment: this pattern
    # of interleaving command.upgrade()/downgrade() (each its own asyncio.run()) with a separate
    # asyncio.run() per check requires disposing models.engine.engine's pool between calls, or a
    # later call gets handed a connection bound to an already-closed event loop.
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'ba_fact'::regclass AND NOT tgisinternal"
            )
        )
        names = {row[0] for row in result.fetchall()}
    await engine.dispose()
    return names


def test_triggers_created_on_upgrade_and_removed_on_downgrade_then_restored():
    cfg = _config()
    command.upgrade(cfg, "head")
    try:
        names = asyncio.run(_trigger_names_on_ba_fact())
        assert TRIGGER_NAMES <= names

        command.downgrade(cfg, "-1")
        names = asyncio.run(_trigger_names_on_ba_fact())
        assert not (TRIGGER_NAMES & names)
    finally:
        command.upgrade(cfg, "head")
        names = asyncio.run(_trigger_names_on_ba_fact())
        assert TRIGGER_NAMES <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/business_analyst/test_trigger_migration.py -v`
Expected: FAIL — `command.upgrade(cfg, "head")` stops at `ba_0001_fact_store_tables`, no
`ba_0002` revision exists yet, so `TRIGGER_NAMES <= names` is false (empty set).

- [ ] **Step 3: Write the implementation**

Create `alembic/versions/ba_0002_fact_triggers.py`:

```python
"""Append-only enforcement on ba_fact: block UPDATE/DELETE (row trigger) and TRUNCATE
(statement trigger — row-level triggers don't fire on TRUNCATE).

Revision ID: ba_0002_fact_triggers
Revises: ba_0001_fact_store_tables
Create Date: 2026-08-03
"""
from alembic import op

revision = "ba_0002_fact_triggers"
down_revision = "ba_0001_fact_store_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ba_fact_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ba_fact is append-only: % is not permitted (id=%)', TG_OP, OLD.id;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER ba_fact_no_update_delete
        BEFORE UPDATE OR DELETE ON ba_fact
        FOR EACH ROW EXECUTE FUNCTION ba_fact_block_mutation();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ba_fact_block_truncate() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ba_fact is append-only: TRUNCATE is not permitted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER ba_fact_no_truncate
        BEFORE TRUNCATE ON ba_fact
        FOR EACH STATEMENT EXECUTE FUNCTION ba_fact_block_truncate();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ba_fact_no_truncate ON ba_fact;")
    op.execute("DROP TRIGGER IF EXISTS ba_fact_no_update_delete ON ba_fact;")
    op.execute("DROP FUNCTION IF EXISTS ba_fact_block_truncate();")
    op.execute("DROP FUNCTION IF EXISTS ba_fact_block_mutation();")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/business_analyst/test_trigger_migration.py -v`
Expected: PASS. Confirm manually: `alembic current` should print `ba_0002_fact_triggers (head)`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/ba_0002_fact_triggers.py agents/business_analyst/test_trigger_migration.py
git commit -m "feat(ba): add ba_fact immutability triggers (block UPDATE/DELETE/TRUNCATE)"
```

---

### Task 4: Test DB fixtures (transactional session + schema-at-head guarantee)

**Files:**
- Create: `agents/business_analyst/conftest.py`
- Test: `agents/business_analyst/test_conftest_fixture.py`

**Interfaces:**
- Consumes: `models.engine.engine`, `agents.business_analyst.models.BaProject`, Alembic setup
  (Tasks 1-3).
- Produces: `db_session` (pytest-asyncio fixture — an `AsyncSession` bound to a connection whose
  outer transaction is rolled back after the test, so no test needs to `DELETE` a fact to clean
  up after itself), `ba_project` (a real, flushed `BaProject` row for FK-dependent tests), and a
  session-scoped autouse fixture that guarantees the schema is at `head` before any test in this
  directory runs, regardless of file collection order.

- [ ] **Step 1: Write the failing test**

```python
# agents/business_analyst/test_conftest_fixture.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/business_analyst/test_conftest_fixture.py -v`
Expected: FAIL with `fixture 'db_session' not found`

- [ ] **Step 3: Write the implementation**

```python
# agents/business_analyst/conftest.py
import uuid

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.models import BaProject
from models.engine import engine

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def _ba_schema_at_head():
    """Runs once per test session, before the first test in this directory that needs it —
    guarantees the ba_* schema exists regardless of which test file pytest collects first."""
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


@pytest_asyncio.fixture
async def db_session():
    """An AsyncSession bound to a single connection's transaction, rolled back after the test.
    Because ba_fact blocks DELETE/TRUNCATE, this is the only practical way to clean up test
    data — the transaction is simply never committed."""
    async with engine.connect() as connection:
        async with connection.begin() as outer_txn:
            session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
            try:
                yield session
            finally:
                await session.close()
                await outer_txn.rollback()


@pytest_asyncio.fixture
async def ba_project(db_session):
    project = BaProject(id=str(uuid.uuid4()), org_id=f"org-{uuid.uuid4()}", name="Test Project")
    db_session.add(project)
    await db_session.flush()
    return project
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/business_analyst/test_conftest_fixture.py -v`
Expected: PASS (all 3 tests, and the second test genuinely depends on the first having run
first — confirm with `-p no:randomly` if a random-order plugin is active in this environment)

- [ ] **Step 5: Commit**

```bash
git add agents/business_analyst/conftest.py agents/business_analyst/test_conftest_fixture.py
git commit -m "test(ba): add transactional db_session and ba_project pytest fixtures"
```

---

### Task 5: `facts.py` — `BATenantContext`, `register_source`, `assert_fact`

**Files:**
- Create: `agents/business_analyst/facts.py`
- Test: `agents/business_analyst/test_facts.py`

**Interfaces:**
- Consumes: `agents.business_analyst.models.{BaSource, BaFact}` (Task 1), `db_session` /
  `ba_project` fixtures (Task 4).
- Produces: `BATenantContext(org_id: str, project_id: str)`,
  `async def register_source(ctx, session, *, kind: str, tier: str, content_hash: str, ref: str | None = None, stakeholder_id: str | None = None) -> BaSource`,
  `async def assert_fact(ctx, session, *, subject_type: str, subject_key: str, predicate: str, source_id: str, asserted_by: str, value: dict | None = None, object_type: str | None = None, object_key: str | None = None, run_id: str | None = None, replaces: str | None = None) -> BaFact`
  — Tasks 6 and 7 call both of these by these exact names and signatures.

- [ ] **Step 1: Write the failing test**

```python
# agents/business_analyst/test_facts.py
import pytest

from agents.business_analyst.facts import BATenantContext, assert_fact, register_source


@pytest.mark.asyncio
async def test_register_source_scopes_to_project_and_org(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)

    source = await register_source(
        ctx, db_session, kind="interview", tier="tier_1", content_hash="abc123"
    )

    assert source.id is not None
    assert source.project_id == ba_project.id
    assert source.org_id == ba_project.org_id
    assert source.kind == "interview"


@pytest.mark.asyncio
async def test_assert_fact_creates_an_unapproved_unreplaced_fact(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_2", content_hash="h1")

    fact = await assert_fact(
        ctx,
        db_session,
        subject_type="Actor",
        subject_key="nurse-1",
        predicate="has_goal",
        value={"text": "see stock levels"},
        source_id=source.id,
        asserted_by="test-suite",
    )

    assert fact.id is not None
    assert fact.seq is not None
    assert fact.project_id == ba_project.id
    assert fact.org_id == ba_project.org_id
    assert fact.human_approval is False
    assert fact.replaces is None
    assert fact.value == {"text": "see stock levels"}


@pytest.mark.asyncio
async def test_assert_fact_has_no_human_approval_parameter(db_session, ba_project):
    # human_approval must never be settable from assert_fact's public signature — only
    # approve_fact (Task 7) may produce a fact with human_approval=True.
    import inspect

    sig = inspect.signature(assert_fact)
    assert "human_approval" not in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/business_analyst/test_facts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.business_analyst.facts'`

- [ ] **Step 3: Write the implementation**

```python
# agents/business_analyst/facts.py
"""Repo functions for the BA OS append-only fact store.

See docs/superpowers/specs/2026-08-03-ba-fact-store-design.md. Every function here takes
ctx: BATenantContext first and filters by ctx.org_id in the same query as everything else —
never filter tenant scope in Python after an unscoped read.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.models import BaFact, BaSource


@dataclass(frozen=True)
class BATenantContext:
    """Constructed by hand (or by tests) until a later phase wires it to a real JWT — see
    architecture_plan.md §6.2. The shape is fixed now so no repo function's signature needs to
    change when that phase lands."""

    org_id: str
    project_id: str


async def register_source(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    kind: str,
    tier: str,
    content_hash: str,
    ref: str | None = None,
    stakeholder_id: str | None = None,
) -> BaSource:
    source = BaSource(
        id=str(uuid.uuid4()),
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        kind=kind,
        tier=tier,
        ref=ref,
        stakeholder_id=stakeholder_id,
        captured_at=datetime.now(timezone.utc),
        content_hash=content_hash,
    )
    session.add(source)
    await session.flush()
    return source


async def assert_fact(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    subject_type: str,
    subject_key: str,
    predicate: str,
    source_id: str,
    asserted_by: str,
    value: dict | None = None,
    object_type: str | None = None,
    object_key: str | None = None,
    run_id: str | None = None,
    replaces: str | None = None,
) -> BaFact:
    # No human_approval parameter, deliberately — see module docstring and Task 7's approve_fact.
    fact = BaFact(
        id=str(uuid.uuid4()),
        project_id=ctx.project_id,
        org_id=ctx.org_id,
        subject_type=subject_type,
        subject_key=subject_key,
        predicate=predicate,
        value=value,
        object_type=object_type,
        object_key=object_key,
        source_id=source_id,
        run_id=run_id,
        human_approval=False,
        asserted_at=datetime.now(timezone.utc),
        asserted_by=asserted_by,
        replaces=replaces,
    )
    session.add(fact)
    await session.flush()
    return fact
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/business_analyst/test_facts.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/business_analyst/facts.py agents/business_analyst/test_facts.py
git commit -m "feat(ba): add BATenantContext, register_source, assert_fact"
```

---

### Task 6: `facts.py` — `get_facts` (current-value resolution + tenant isolation)

**Files:**
- Modify: `agents/business_analyst/facts.py`
- Modify: `agents/business_analyst/test_facts.py`

**Interfaces:**
- Consumes: `assert_fact`, `register_source`, `BATenantContext` (Task 5).
- Produces: `async def get_facts(ctx, session, *, subject_key: str | None = None, predicate: str | None = None) -> list[BaFact]`
  — returns only "current" facts (never a fact some other fact's `replaces` points at) for
  `ctx.org_id`/`ctx.project_id`, ordered by `seq`. Task 7's `approve_fact` and Task 8 both rely
  on this being correct.

- [ ] **Step 1: Write the failing test**

```python
# append to agents/business_analyst/test_facts.py
from agents.business_analyst.facts import get_facts


@pytest.mark.asyncio
async def test_get_facts_returns_only_the_current_value_in_a_replaces_chain(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h2")

    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )
    corrected = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="text",
        value={"v": 2}, source_id=source.id, asserted_by="test-suite", replaces=original.id,
    )

    facts = await get_facts(ctx, db_session, subject_key="req-1", predicate="text")

    assert [f.id for f in facts] == [corrected.id]
    assert facts[0].value == {"v": 2}


@pytest.mark.asyncio
async def test_get_facts_walks_multi_level_replaces_chains(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h3")

    v1 = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-2", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )
    v2 = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-2", predicate="text",
        value={"v": 2}, source_id=source.id, asserted_by="test-suite", replaces=v1.id,
    )
    v3 = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-2", predicate="text",
        value={"v": 3}, source_id=source.id, asserted_by="test-suite", replaces=v2.id,
    )

    facts = await get_facts(ctx, db_session, subject_key="req-2", predicate="text")

    assert [f.id for f in facts] == [v3.id]


@pytest.mark.asyncio
async def test_get_facts_never_returns_another_orgs_rows_even_with_a_matching_project_id(
    db_session, ba_project
):
    real_ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(real_ctx, db_session, kind="document", tier="tier_1", content_hash="h4")
    await assert_fact(
        real_ctx, db_session, subject_type="Requirement", subject_key="req-3", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    # A forged context: correct project_id, wrong org_id — must return nothing, proving the
    # org filter (not just the project filter) is enforced in the query.
    forged_ctx = BATenantContext(org_id="org-attacker", project_id=ba_project.id)
    facts = await get_facts(forged_ctx, db_session, subject_key="req-3")

    assert facts == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/business_analyst/test_facts.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_facts'`

- [ ] **Step 3: Write the implementation**

```python
# add to agents/business_analyst/facts.py, alongside the existing imports:
from sqlalchemy import select
from sqlalchemy.orm import aliased

# add function:
async def get_facts(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    subject_key: str | None = None,
    predicate: str | None = None,
) -> list[BaFact]:
    """Returns current facts only: a fact is excluded iff some other fact's `replaces` points
    at it, which correctly resolves chains of any length without a recursive CTE — a fact with
    nothing pointing at it is, by construction, the most recent version."""
    Replacement = aliased(BaFact)
    superseded = (
        select(Replacement.id).where(Replacement.replaces == BaFact.id).exists()
    )

    stmt = (
        select(BaFact)
        .where(BaFact.org_id == ctx.org_id, BaFact.project_id == ctx.project_id)
        .where(~superseded)
        .order_by(BaFact.seq)
    )
    if subject_key is not None:
        stmt = stmt.where(BaFact.subject_key == subject_key)
    if predicate is not None:
        stmt = stmt.where(BaFact.predicate == predicate)

    result = await session.execute(stmt)
    return list(result.scalars().all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/business_analyst/test_facts.py -v`
Expected: PASS (all 6 tests so far)

- [ ] **Step 5: Commit**

```bash
git add agents/business_analyst/facts.py agents/business_analyst/test_facts.py
git commit -m "feat(ba): add get_facts with replaces-chain resolution and tenant isolation"
```

---

### Task 7: `facts.py` — `approve_fact`

**Files:**
- Modify: `agents/business_analyst/facts.py`
- Modify: `agents/business_analyst/test_facts.py`

**Interfaces:**
- Consumes: `assert_fact`, `get_facts`, `BATenantContext` (Tasks 5-6).
- Produces: `async def approve_fact(ctx, session, *, fact_id: str, asserted_by: str) -> BaFact` —
  the only function in this module that can produce a fact with `human_approval=True`.

- [ ] **Step 1: Write the failing test**

```python
# append to agents/business_analyst/test_facts.py
from agents.business_analyst.facts import approve_fact


@pytest.mark.asyncio
async def test_approve_fact_inserts_a_new_row_and_never_touches_the_original(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h5")
    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-4", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    approved = await approve_fact(ctx, db_session, fact_id=original.id, asserted_by="ba-lead")

    assert approved.id != original.id
    assert approved.human_approval is True
    assert approved.replaces == original.id
    assert approved.value == original.value
    assert approved.subject_key == original.subject_key

    # The original row is untouched — re-fetch it to be sure nothing mutated it in place.
    from sqlalchemy import select

    from agents.business_analyst.models import BaFact

    result = await db_session.execute(select(BaFact).where(BaFact.id == original.id))
    original_reloaded = result.scalar_one()
    assert original_reloaded.human_approval is False


@pytest.mark.asyncio
async def test_approve_fact_becomes_the_current_value(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h6")
    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-5", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    approved = await approve_fact(ctx, db_session, fact_id=original.id, asserted_by="ba-lead")

    facts = await get_facts(ctx, db_session, subject_key="req-5", predicate="text")
    assert [f.id for f in facts] == [approved.id]


@pytest.mark.asyncio
async def test_approve_fact_rejects_a_fact_from_another_tenant(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h7")
    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-6", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    forged_ctx = BATenantContext(org_id="org-attacker", project_id=ba_project.id)
    with pytest.raises(ValueError):
        await approve_fact(forged_ctx, db_session, fact_id=original.id, asserted_by="attacker")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/business_analyst/test_facts.py -v`
Expected: FAIL with `ImportError: cannot import name 'approve_fact'`

- [ ] **Step 3: Write the implementation**

```python
# add to agents/business_analyst/facts.py

async def approve_fact(
    ctx: BATenantContext,
    session: AsyncSession,
    *,
    fact_id: str,
    asserted_by: str,
) -> BaFact:
    """The only function that can produce a fact with human_approval=True. ba_fact is
    immutable (a DB trigger blocks UPDATE unconditionally), so approval cannot flip a column
    on the existing row — instead this reads the target fact and inserts a new one, identical
    except human_approval=True and replaces=fact_id."""
    original = await session.get(BaFact, fact_id)
    if original is None or original.org_id != ctx.org_id or original.project_id != ctx.project_id:
        raise ValueError(f"fact {fact_id} not found in project {ctx.project_id}")

    approved = BaFact(
        id=str(uuid.uuid4()),
        project_id=original.project_id,
        org_id=original.org_id,
        subject_type=original.subject_type,
        subject_key=original.subject_key,
        predicate=original.predicate,
        value=original.value,
        object_type=original.object_type,
        object_key=original.object_key,
        source_id=original.source_id,
        run_id=original.run_id,
        human_approval=True,
        asserted_at=datetime.now(timezone.utc),
        asserted_by=asserted_by,
        replaces=original.id,
    )
    session.add(approved)
    await session.flush()
    return approved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/business_analyst/test_facts.py -v`
Expected: PASS (all 9 tests so far)

- [ ] **Step 5: Commit**

```bash
git add agents/business_analyst/facts.py agents/business_analyst/test_facts.py
git commit -m "feat(ba): add approve_fact as a replaces-linked insert, never an update"
```

---

### Task 8: Behavioral immutability tests

**Files:**
- Create: `agents/business_analyst/test_fact_immutability.py`

**Interfaces:**
- Consumes: `register_source`, `assert_fact`, `BATenantContext` (Task 5), `db_session`/
  `ba_project` fixtures (Task 4), the triggers from Task 3.
- Produces: nothing new — this is pure verification that the two triggers from Task 3 actually
  block writes, using real rows created through the normal `facts.py` API rather than
  hand-crafted SQL fixtures.

- [ ] **Step 1: Write the failing test**

```python
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


@pytest.mark.asyncio
async def test_delete_ba_fact_raises(db_session, ba_project):
    fact = await _make_fact(db_session, ba_project, "immutable-delete")

    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text("DELETE FROM ba_fact WHERE id = :id"), {"id": fact.id})


@pytest.mark.asyncio
async def test_truncate_ba_fact_raises(db_session, ba_project):
    await _make_fact(db_session, ba_project, "immutable-truncate")

    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text("TRUNCATE ba_fact"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/business_analyst/test_fact_immutability.py -v`
Expected: FAIL — if Tasks 1-7 are already done, this actually may already pass once written
(the triggers already exist from Task 3). Verify it would have failed before Task 3 by
temporarily running `alembic downgrade -1`, confirming a `DBAPIError` is NOT raised, then
`alembic upgrade head` again before proceeding. This is the one task in this plan where "watch
it fail first" means watching the trigger's *absence* fail to raise, not a Python import error.

- [ ] **Step 3: Confirm implementation (no new production code — this task is pure verification)**

Nothing to write beyond the test file above; Task 3 already implemented the triggers.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/business_analyst/test_fact_immutability.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/business_analyst/test_fact_immutability.py
git commit -m "test(ba): verify ba_fact triggers block UPDATE/DELETE/TRUNCATE on real rows"
```

---

## Final check

Run the full suite for this phase and confirm nothing outside it regressed:

```bash
pytest agents/business_analyst/ -v
pytest tests/ app/ agents/ -x -q
```

Both must pass before this phase is considered done — the design doc's Verification section
requires the second command not to regress, and it's the one command that catches an accidental
collision between BA's new `alembic.ini`/`alembic/` at repo root and anything else that might
scan the repo root.
