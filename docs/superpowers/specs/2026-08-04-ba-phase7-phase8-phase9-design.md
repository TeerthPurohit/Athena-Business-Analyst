# BA OS Phase 7, Phase 8, & Phase 9 — Design

Status: Proposed
Parent specs: [agents/business_analyst/specification.md](../../../agents/business_analyst/specification.md), [agents/business_analyst/architecture_plan.md](../../../agents/business_analyst/architecture_plan.md) (§0, §2.1, §3, §4, §6, §11.6)

## Scope

This document specifies the architecture and implementation design for:
- **Phase 7 (Security Layer)**: Strict JWT-only authentication (`get_ba_tenant_context`), tenant isolation, dedicated `ba_embeddings` vector table, `before_flush` ORM write-guard for privileged fields (`human_approval`, `source_tier`, `evidence_confidence`), approval endpoints (`POST /facts/{id}/approve`), Unicode prompt-injection sanitization, and Redis channel namespacing.
- **Phase 8 (Semantic Planner)**: Natural language user intent parsing to `ProjectIR` schema (`extra="forbid"`), strictly separating intent understanding from execution ordering and capability selection.
- **Phase 9 (Acquisition & Derivation Capabilities)**: 4 acquisition capabilities (`document_analysis`, `interview`, `compliance_lookup`, `market_research`) and 10 derivation capabilities (`derive_requirements`, `model_process`, `derive_edge_cases`, `derive_nfr`, `derive_data_model`, `derive_stakeholders`, `derive_raci`, `derive_risks`, `derive_glossary_terms`, `derive_options`). Enforces **structured field emission** (not prose) and automatic `derived_from` / `traces_to` relation facts.

---

## 1. Phase 7 — Security Layer Design

### 1.1 Tenant Auth (`api/security.py`)
- `get_ba_tenant_context(request: Request)`:
  - Validates JWT from `Authorization: Bearer <token>` header ONLY.
  - **No fallback headers** (`X-Organization-Id`, etc. ignored).
  - **No query parameter overrides**.
  - **No default company fallback**. Invalid/missing token raises HTTP 401 immediately.
  - `exp` claim is strictly required. Expired token raises HTTP 401.
  - `sub` is user ID — **rejected** as tenant key.
  - `workspaceId` is rejected as tenant key.
  - Claims must include explicit `org_id` (or `organization_id`). Conflicting claims raise 401.
  - Foreign project lookup returns **404 Not Found**, never 403 (prevents enumeration).

### 1.2 Dedicated `ba_embeddings` Table & Vector Isolation
- Model `BaEmbedding` in `agents/business_analyst/models/embedding.py`:
  - `id`, `project_id`, `org_id NOT NULL`, `entity_type`, `entity_id`, `vector` (1536 dims), `created_at`.
  - `UNIQUE(entity_type, entity_id)` to prevent duplicate embeddings.
  - Alembic migration `ba_0007_embeddings_table.py`.
  - Queries apply `WHERE org_id = :org_id` in the **same statement** as `ORDER BY vector <-> query_vector LIMIT k`.

### 1.3 ORM `before_flush` Guard for Privileged Fields (`api/security.py`)
- Listens to SQLAlchemy session `before_flush` events.
- Inspects modified `BaFact` and `BaDeliverableInstance` objects.
- If `human_approval`, `source_tier`, or `evidence_confidence` is modified outside the allowed approval/scoring functions (or unauthorized route context), raises `PermissionError` / aborts transaction.
- Approval verb: `POST /api/ba/facts/{id}/approve` (creates new `replaces`-linked row with `human_approval=True`).

### 1.4 Unicode Sanitization & Injection Guards
- Strips zero-width characters (`\u200B-\u200D`, `\uFEFF`), bidi overrides (`\u202E`, etc.), and Unicode tag characters (`U+E0000–U+E007F`).
- Sanitizes untrusted document/interview content before passing to prompt contexts.

---

## 2. Phase 8 — Semantic Planner Design

### 2.1 Pydantic `ProjectIR` Schema (`ir.py`)
- Pydantic models with `model_config = ConfigDict(extra="forbid")`:
  - `Objective`: `id`, `description`, `priority`, `category`
  - `Entity`: `name`, `type`, `attributes`
  - `Goal`: `id`, `name`, `description`, `target_metrics`
  - `ProjectScope`: `in_scope`, `out_of_scope`, `constraints`
  - `ProjectIR`: `project_name`, `objectives`, `entities`, `goals`, `scope`

### 2.2 Semantic Planner (`semantic_planner.py`)
- `build_project_ir(user_request: str) -> ProjectIR`:
  - Uses prompt seeded in database (`agent_prompts` table, `agent_id="9"`, prompt key `ba_semantic_planner_v1`).
  - Converts user request into `ProjectIR`.
  - **Does NOT** select capability names or schedule execution order (that is the Deterministic Planner's job).

---

## 3. Phase 9 — Acquisition & Derivation Capabilities Design

### 3.1 Capability Base & Registration (`capability.py`)
- Acquisition Capabilities (`capabilities/acquisition/`):
  - `document_analysis`: parses documents into raw facts.
  - `interview`: extracts stakeholder facts & quotes.
  - `compliance_lookup`: fetches regulatory & compliance constraints.
  - `market_research`: extracts industry benchmarks & competitor facts.
- Derivation Capabilities (`capabilities/derivation/`):
  - `derive_requirements`: **Load-bearing prerequisite** — emits structured fields `{actor, capability, object, benefit, trigger, constraints[]}` (not prose).
  - `model_process`: infers process steps, actors, and decision points.
  - `derive_edge_cases`: infers failure modes & edge case risk nodes.
  - `derive_nfr`: extracts non-functional requirements & quality targets.
  - `derive_data_model`: infers entity-attribute-relationship data dictionary facts.
  - `derive_stakeholders`: extracts stakeholder roles and authority levels.
  - `derive_raci`: derives RACI matrix relations (`responsible`, `accountable`, `consulted`, `informed`).
  - `derive_risks`: generates risk log nodes & impact scores.
  - `derive_glossary_terms`: derives domain vocabulary and definitions.
  - `derive_options`: infers solution trade-offs & option analysis.

### 3.2 Free Traceability Mechanic
- Every derivation capability writes `derived_from` / `traces_to` relation facts pointing new nodes back to input nodes/sources as a mandatory side effect.

---

## File Changes Summary

### New Files
1. `agents/business_analyst/models/embedding.py`
2. `alembic/versions/ba_0007_embeddings_table.py`
3. `agents/business_analyst/api/__init__.py`
4. `agents/business_analyst/api/security.py`
5. `agents/business_analyst/prompt_seeds/ba_prompts.py`
6. `agents/business_analyst/capabilities/acquisition/__init__.py`
7. `agents/business_analyst/capabilities/acquisition/document_analysis.py`
8. `agents/business_analyst/capabilities/acquisition/interview.py`
9. `agents/business_analyst/capabilities/acquisition/compliance_lookup.py`
10. `agents/business_analyst/capabilities/acquisition/market_research.py`
11. `agents/business_analyst/capabilities/derivation/__init__.py`
12. `agents/business_analyst/capabilities/derivation/derive_requirements.py`
13. `agents/business_analyst/capabilities/derivation/model_process.py`
14. `agents/business_analyst/capabilities/derivation/derive_edge_cases.py`
15. `agents/business_analyst/capabilities/derivation/derive_nfr.py`
16. `agents/business_analyst/capabilities/derivation/derive_data_model.py`
17. `agents/business_analyst/capabilities/derivation/derive_stakeholders.py`
18. `agents/business_analyst/capabilities/derivation/derive_raci.py`
19. `agents/business_analyst/capabilities/derivation/derive_risks.py`
20. `agents/business_analyst/capabilities/derivation/derive_glossary.py`
21. `agents/business_analyst/capabilities/derivation/derive_options.py`
22. `agents/business_analyst/test_security.py`
23. `agents/business_analyst/test_semantic_planner.py`
24. `agents/business_analyst/test_capabilities.py`

### Modified Files
1. `agents/business_analyst/ir.py`
2. `agents/business_analyst/semantic_planner.py`
3. `agents/business_analyst/specification.md` (ticking checkboxes as done)
