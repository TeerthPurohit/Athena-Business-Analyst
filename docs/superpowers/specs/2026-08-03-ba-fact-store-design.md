# BA OS Phase 1: Fact Store — Design

Status: Approved
Parent specs: [agents/business_analyst/specification.md](../../../agents/business_analyst/specification.md),
[agents/business_analyst/architecture_plan.md](../../../agents/business_analyst/architecture_plan.md)
(§4.1, §6.3, §9)

## Scope

First implementation slice of the Business Analyst OS: the append-only fact store only —
`ba_project`, `ba_source`, `ba_fact`, the immutability trigger, and `facts.py`'s repo functions.
Graph projection, planner, executor, quality engine, capabilities, and API routes are later
phases and out of scope here.

## Why this slice first

Every other layer of the BA OS (§3 of the architecture plan) reads from or writes to the fact
store — graph projection rebuilds from it, the planner reads graph state derived from it, the
quality engine scores it. Nothing else can be meaningfully built or tested without it existing
first.

## Conventions adopted (deviations from the architecture plan, decided during brainstorming)

- **Alembic, not ad-hoc migration scripts.** The root project currently has no migration tool —
  schema is created via `Base.metadata.create_all()` in `models/engine.py:87`, and changes to
  existing tables go through hand-written `migrations/00N_*.py` scripts (see `007`/`008`). BA
  introduces Alembic at root, but **scoped to `ba_*` tables only**: `alembic/env.py` imports
  `agents.business_analyst.models.Base.metadata`, a separate `Base` from the shared
  `models.base.Base`, so `autogenerate` never diffs tables `create_all()` already owns.
  `init_db_tables()` is untouched — it keeps creating every non-BA table exactly as it does today.
- **Alembic's version-tracking table is also scoped: `version_table="ba_alembic_version"`,
  not the default `alembic_version`.** Discovered during implementation, not anticipated in the
  original design: root's `DATABASE_URL` and `agents/universal-agent`'s point at the same
  physical Neon database, and universal-agent already runs its own Alembic history against that
  DB's default `alembic_version` table (currently at `08587509f2ea`). Using the default table
  name for BA's migrations would silently share — and corrupt — universal-agent's revision
  bookkeeping the first time `alembic upgrade head` runs for BA. Both `context.configure()` calls
  in `alembic/env.py` (offline and online) pass `version_table="ba_alembic_version"`, isolating
  BA's migration history the same way its schema is already isolated by the separate `Base`.
- **Models live in `agents/business_analyst/models.py`**, not root `models/`, consistent with BA
  being its own package (CLAUDE.md override of architecture_plan.md §1/§8).
- **`ba_project` is a new minimal catalog table**, not in the original spec. `project_id` is
  referenced by `ba_source`/`ba_fact` throughout the architecture plan but no table defining a
  project was ever specified. Added here: `id · org_id · name · created_at`. Richer project
  metadata can be added later without migrating the fact tables.
- **IDs are `String(36)` UUIDs**, matching the root convention (`models/plan.py`, `models/agent.py`)
  rather than universal-agent's native `PG_UUID`.
- **`org_id` is an opaque string column, no local FK** — matches `models/agent.py`,
  `models/campaign_plan.py`: Node/Prisma owns organizations, Python never joins against them
  locally (see memory `node-python-architecture-split`).
- **Retraction is a forward-only pointer, not `retracted_by`.** The architecture plan's §4.1
  column list includes `retracted_by` on `ba_fact`, which reads as "the old fact names the fact
  that retracted it" — but that requires an `UPDATE` on the old row after it's already been
  inserted, which the immutability trigger (below) blocks unconditionally. There is no carve-out
  for it in §4.1 or §9's "`UPDATE`/`DELETE`/`TRUNCATE` all abort loudly" test. Fixed here: the
  *new* fact carries `replaces: fact_id | None`, set once at its own insert time, pointing
  backward at the fact it supersedes. "Is this fact retracted" becomes a query (does any other
  fact's `replaces` point at it), not a stored back-reference. This keeps every row's columns
  fixed forever at insert time, which is what the trigger actually enforces.

## Schema

```python
# agents/business_analyst/models.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """BA's own declarative base — deliberately NOT models.base.Base. Alembic's env.py points
    target_metadata at this Base only, so autogenerate can never see (and can never emit a
    spurious CREATE TABLE for) any non-ba_* table, even if one is added later without a matching
    Alembic revision (root's other tables are still created via create_all(), untouched by this
    Alembic setup — see "Conventions adopted" above)."""

class BaProject(Base):
    __tablename__ = "ba_project"
    id: str            # String(36), PK, default=uuid4
    org_id: str         # String(36), not null, indexed
    name: str           # String(255), not null
    created_at: datetime

class BaSource(Base):
    __tablename__ = "ba_source"
    id: str              # String(36), PK
    project_id: str       # FK -> ba_project.id, not null, indexed
    org_id: str           # String(36), not null, indexed — denormalized so tenant checks
                          # never require a join (S3 lesson: filter in the same statement)
    kind: str             # 'document' | 'interview' | 'compliance_lookup' | 'market_research'
                          # | 'llm_inference' | ...
    tier: str             # source-tier, drives the (later) Quality Engine's confidence ceiling
    ref: str | None       # pointer to underlying artifact (blob key / URL / transcript id)
    stakeholder_id: str | None
    captured_at: datetime
    content_hash: str     # sha256, for dup-source detection

class BaFact(Base):
    __tablename__ = "ba_fact"
    id: str                 # String(36), PK
    project_id: str          # FK -> ba_project.id, not null, indexed
    org_id: str              # String(36), not null, indexed
    seq: int                 # GENERATED ALWAYS AS IDENTITY — the ONLY ordering tiebreaker,
                             # never created_at (wall-clock ties/skew are non-deterministic)
    subject_type: str
    subject_key: str
    predicate: str
    value: JSONB | None       # attribute fact — structured, per architecture_plan.md §2.1
                              # ("derivation must emit structured fields, not prose")
    object_type: str | None   # relation fact
    object_key: str | None
    source_id: str            # FK -> ba_source.id, not null
    run_id: str | None
    human_approval: bool      # default False. Privileged field (§6.3) — writable only through
                              # the future approval endpoint's repo function, never a generic
                              # update path. Included now (not deferred to the Quality Engine
                              # phase) because it's a direct per-fact write, not a computed
                              # score, and retrofitting the privileged-field guard onto an
                              # already-shipped table is a real risk to avoid.
    asserted_at: datetime
    asserted_by: str          # prompt id/version or user id — never free text
    replaces: str | None      # FK -> ba_fact.id, nullable, self-referential. Set once, at
                              # insert time, on the NEW fact — never written to the old row.
```

Indexes: `(project_id, subject_type, subject_key)` for future graph-projection reads,
`(project_id, seq)` for ordered replay, `(org_id)` on both `ba_source` and `ba_fact`.

**Deliberately deferred, not in this table:** `evidence_confidence`. It's a computed score over
corroborating facts (architecture_plan.md §5's formula), not a stored fact property — storing a
snapshot here now would let it drift from the formula that's supposed to be the single source of
truth. `source_tier` is not duplicated per-fact either; it lives once on `ba_source.tier` and
`ba_fact.source_id` joins to it.

## File-growth thresholds

`models.py` and `facts.py` stay flat, single files, for this phase (see "Conventions adopted"
above for why splitting now is premature). "Split later" needs a concrete trigger or it never
happens and never gets debated on merit either — so:

- `models.py` splits into per-entity files when **`ba_node`/`ba_edge` land (phase 2) or it
  passes ~250 LOC**, whichever comes first. Not a class count — this codebase's models carry
  long rationale docstrings (see `models/plan.py`), so LOC is the honest proxy, not class count.
- `facts.py` splits when **another entity's read/write logic starts landing in it** — concretely,
  if `graph.py`'s projection rebuild needs helpers and the path of least resistance is adding
  them to `facts.py` instead of keeping them in `graph.py`. That's the actual failure mode a
  repository-layer split was meant to prevent; naming it directly is cheaper than the layer.

Both are mechanical splits (independent classes/functions, no shared internal state) — expected
to take minutes when the trigger actually fires, not a refactor to plan around in advance.

## Immutability

Two triggers on `ba_fact`:

1. A row-level `BEFORE UPDATE OR DELETE` trigger that raises, blocking both operations
   unconditionally.
2. A statement-level `BEFORE TRUNCATE` trigger (row-level triggers do not fire on `TRUNCATE`).

A "correction" is: insert a new fact with `replaces` set to the old fact's id. `get_facts`
resolves "current value" per `(subject_key, predicate)` by taking the highest-`seq` fact in each
replacement chain — i.e. a fact is superseded iff some other fact's `replaces` points at it.
Nothing is ever written to an existing row; every column of every fact is fixed at insert time,
which is exactly what the trigger enforces.

"Highest-`seq` in the chain" and "nothing points at me" are only the same thing for a strictly
linear chain — if two facts ever set `replaces` to the same target, the chain forks and both
tips would read as current. A partial unique index on `ba_fact.replaces` (unique where
`replaces IS NOT NULL`, `ba_0003_unique_replaces`) is what guarantees chains stay linear, making
the `NOT EXISTS`/highest-`seq` framing actually correct rather than merely usually correct.

Honest limit (documented, not solved here): the trigger stops the application, not a privileged
DB operator with `ALTER TABLE ... DISABLE TRIGGER`. A real boundary needs a second, non-owner DB
role — a deployment change, out of scope for this phase.

## `facts.py` API

```python
@dataclass
class BATenantContext:
    org_id: str
    project_id: str

async def assert_fact(ctx: BATenantContext, session, *, subject_type: str, subject_key: str,
                       predicate: str, value: dict | None = None, object_type: str | None = None,
                       object_key: str | None = None, source_id: str, run_id: str | None = None,
                       asserted_by: str, replaces: str | None = None) -> BaFact: ...

async def get_facts(ctx: BATenantContext, session, *, subject_key: str | None = None,
                     predicate: str | None = None) -> list[BaFact]: ...

async def register_source(ctx: BATenantContext, session, *, kind: str, tier: str,
                           ref: str | None, content_hash: str,
                           stakeholder_id: str | None = None) -> BaSource: ...

async def approve_fact(ctx: BATenantContext, session, *, fact_id: str, asserted_by: str) -> BaFact:
    """The only path that may produce a fact with human_approval=True. ba_fact is immutable —
    the trigger blocks UPDATE unconditionally — so approval cannot flip a column on the existing
    row. Instead this reads the target fact, inserts a new fact with identical subject/predicate/
    value/object/source_id, human_approval=True, and replaces=fact_id, and returns the new row.
    Approval is a correction, structurally identical to assert_fact(replaces=...), restricted to
    only ever changing human_approval."""
```

Every function takes `ctx: BATenantContext` first and filters by `ctx.org_id` in the same query
as the rest of the `WHERE` clause. The real JWT-parsing constructor for `BATenantContext` is a
later phase (architecture_plan.md §6.2); until then, `ctx` is constructed directly by callers and
tests. Building the shape now avoids retrofitting every repo function's signature later.

## Verification (this phase)

`agents/business_analyst/test_facts.py`, against real Postgres (a trigger cannot be tested
against sqlite):

- `UPDATE`/`DELETE`/`TRUNCATE ba_fact` all abort loudly.
- `assert_fact(replaces=...)` produces a new row and never mutates the row it replaces.
- `get_facts` never returns rows from another `org_id`, including when called with a
  `project_id` that happens to collide across tenants (id collision, not just missing filter).
- `approve_fact` is the only function that can produce a fact with `human_approval=True`; it does
  so by inserting a new row with `replaces` set to the original, never by updating the original.
  `assert_fact` called directly can never set `human_approval=True` — that parameter doesn't
  exist on its signature.

## Out of scope for this phase

Graph projection (`ba_node`/`ba_edge`), the planner, executor, quality engine, capabilities,
`BATenantContext`'s real JWT source, `ba_embeddings`, and all 17 deliverables. Each is its own
future phase per the build order agreed at the start of this design session.
