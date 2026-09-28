# BA OS Phase 5 & Phase 6: Quality Engine & Executor — Design

Status: Proposed
Parent specs: [agents/business_analyst/specification.md](../../../agents/business_analyst/specification.md), [agents/business_analyst/architecture_plan.md](../../../agents/business_analyst/architecture_plan.md) (§5, §7, §9)

## Scope

This design specifies the implementation of **Phase 5 (Quality Engine)** and **Phase 6 (Executor)** for the Business Analyst Operating System (BA OS).

- **Phase 5 (Quality Engine)**: Deterministic quality scoring (`evidence_confidence`), multi-dimensional graph health assessment (completeness, consistency, traceability, ambiguity, risk), advisory LLM assessments, decay rules, kNN-gated contradiction candidate detection, and JSON configuration.
- **Phase 6 (Executor)**: Celery `ba` task queue execution, lease management (`lease_expires_at`), fencing-conditional commits, atomic single-transaction fact commits, restart recovery via lease reclamation, and docker-compose `worker-ba` & `beat` service definitions.

---

## Architecture & Principles

### 1. Phase 5 — Quality Engine

#### 1.1 Deterministic Evidence Confidence Scoring (`quality/scoring.py`)
- **Formula**:
  $$\text{score} = \text{ceiling} \times (1 - e^{-k \cdot N_{\text{eff}}}) \times \prod (1 - \text{penalty})$$
  where $k = \ln 2 \approx 0.693147$.
- **Source-class ceiling**:
  - Highest tier among independent sources determining the ceiling (Tier 1 = 0.98, Tier 2 = 0.90, Tier 3 = 0.80, Tier 4 = 0.70, Tier 5 = 0.60, Tier 10 / LLM-only = 0.40).
  - Facts backed *only* by `llm_inference` sources are capped at `0.1000` (or `0.4000` max ceiling).
- **Corroboration ($N_{\text{eff}}$)**:
  - Multiple quotes from a single document/transcript count as **1 source** (deduplicated by `source_id`).
  - LLM sources are capped at $N_{\text{eff}} = 1.0$ total.
- **Contradiction Penalty**:
  - Each confirmed contradiction reduces confidence by a factor of $(1 - 0.30)$.
- **Free-Text Isolation**:
  - The scorer **never reads prose text** to determine score (prevents prompt injection content from inflating trust). Input is strictly structural: `source_kind`, `source_tier`, distinct `source_id` count, `human_approval` flag.
- **Predicate Decay**:
  - Exponential decay applies **only** to present-state (`as-is`) predicates (e.g. current workflow speed, legacy system throughput).
  - Decision / definition (`to-be`) predicates (e.g. business goals, requirement specifications) **never decay**.

#### 1.2 Multi-Dimensional Graph Scoring (`quality/dimensions.py`)
- Evaluates 5 core quality dimensions:
  1. **Completeness**: ratio of populated required properties on nodes against ontology definitions.
  2. **Consistency**: absence of unresolved contradictions in the knowledge graph.
  3. **Traceability**: proportion of requirement nodes connected via `derived_from` / `traces_to` edges back to source facts.
  4. **Ambiguity**: presence of `<UNSPECIFIED: x>` placeholders or ambiguous requirements.
  5. **Risk**: density of unmitigated risk nodes linked to high-criticality requirements.

#### 1.3 Contradiction Candidate Detection & Fingerprint Cache (`quality/similarity.py`)
- $O(n^2)$ pair comparison avoided by kNN candidate gating ($k=12$).
- Pair fingerprint cache keyed by `(sha(text_a), sha(text_b), prompt_version, model)`.

#### 1.4 Versioned Config (`quality/config/ba_scoring_v1.json`)
- Versioned, sha-pinned configuration file storing scoring constants with confidence metadata.

#### 1.5 Advisory LLM Assessment (`quality/engine.py`)
- LLM quality assessments stored in `llm_assessment` property/model, advisory ONLY. Can trigger a human review task but **never mutates or overrides `evidence_confidence`**.

---

### 2. Phase 6 — Executor Engine

#### 2.1 Task & Run Models (`agents/business_analyst/models/task.py`)
- `BaRun`: `id`, `project_id`, `org_id`, `status` (`pending`, `running`, `completed`, `failed`), `created_at`, `updated_at`.
- `BaRunTask`: `id`, `run_id`, `capability_id`, `org_id`, `status` (`pending`, `running`, `completed`, `failed`), `lease_expires_at` (datetime), `worker_id`, `idempotency_key` (`(run_id, capability_id)`), `error`.

#### 2.2 Celery Queue & Task Dispatch (`tasks/ba_tasks.py` & `executor.py`)
- Celery `ba` queue dedicated worker task `execute_ba_capability`.
- **Fencing & Leases**:
  - Before starting execution, task acquires lease by setting `status = 'running'`, `lease_expires_at = now() + lease_ttl` where `lease_expires_at` was NULL or expired.
- **Atomic Single-Transaction Fact Commit**:
  - Facts produced by capability are buffered in worker memory during execution.
  - On capability completion, buffered facts (`BaFact` rows) and task status update (`BaRunTask.status = 'completed'`) are committed to Postgres in **one single atomic database transaction**.
  - If worker crashes or dies (`SIGKILL`) mid-capability, zero partial facts are left in the database.
- **Beat Service & Stalled Task Reaper (`reap_stalled_runs`)**:
  - Background task running every 60s reclaims tasks with `status = 'running'` and `lease_expires_at < now()`, setting status back to `pending`.

#### 2.3 Docker Compose Services
- Add `worker-ba` worker service running Celery `ba` queue.
- Add `beat` service to run periodic tasks (including `reap_stalled_runs`).

---

## File Changes Summary

### New Files
1. `agents/business_analyst/quality/config/ba_scoring_v1.json`
2. `agents/business_analyst/models/task.py`
3. `alembic/versions/ba_0006_run_task_tables.py`
4. `agents/business_analyst/test_quality.py`
5. `agents/business_analyst/test_executor.py`

### Modified Files
1. `agents/business_analyst/quality/scoring.py`
2. `agents/business_analyst/quality/dimensions.py`
3. `agents/business_analyst/quality/similarity.py`
4. `agents/business_analyst/quality/ambiguity.py`
5. `agents/business_analyst/quality/engine.py`
6. `agents/business_analyst/tasks/ba_tasks.py`
7. `agents/business_analyst/executor.py`
8. `agents/universal-agent/tasks/celery_app.py`
9. `docker-compose.yml`
10. `agents/business_analyst/specification.md` (ticking Phase 5 & Phase 6 checkboxes upon task completion)
