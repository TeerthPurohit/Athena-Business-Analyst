# BA OS Platform Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make meeting-transcript requirement extraction produce source-backed categorized requirements, ambiguity agenda items, deterministic user stories, and deterministic Gherkin scenarios.

**Architecture:** Keep the append-only fact store as the source of truth. A bounded LLM semantic pass returns schema-validated extraction candidates from source spans; persistence writes evidence, requirements, and gaps atomically. Projection functions consume facts only and therefore cannot invent requirements or acceptance thresholds.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy async, existing agent-prompt database, pytest/pytest-asyncio.

## Global Constraints

- Preserve `BATenantContext` filtering on every fact/source read and write.
- Prompts are database configuration seeded through `prompt_seeds`; no inline fallback prompt.
- Facts are append-only. Approval, confidence, and source tier stay outside extractor-controlled request data.
- No `eval()` and no LLM call in deterministic renderers or the planner.
- Never infer missing fields: create a `Gap` fact with a reason and stakeholder-facing question.
- One requirement must link to at least one source-span fact. Every story, scenario, and agenda item must link to one requirement or gap.
- Use existing dependencies; do not add an orchestration framework, MCP server, vector database, or generic agent runtime.

---

### Task 1: Add schema-validated transcript requirement contracts

**Files:**
- Create: `agents/business_analyst/requirements.py`
- Create: `agents/business_analyst/test_requirements.py`

**Interfaces:**
- Produces `RequirementCategory`, `SourceSpan`, `ExtractedRequirement`, `RequirementExtraction`, `missing_requirement_fields()`, `render_user_story()`, and `render_gherkin()`.
- Consumed by extraction persistence and projection renderers.

- [ ] **Step 1: Write failing contract tests**

```python
from agents.business_analyst.requirements import ExtractedRequirement, RequirementCategory, render_gherkin, render_user_story


def test_functional_requirement_projects_story_and_gherkin():
    requirement = ExtractedRequirement(
        external_key="inventory-approval",
        category=RequirementCategory.FUNCTIONAL,
        stakeholder="warehouse manager",
        task="approve stock adjustments",
        object="inventory adjustment",
        benefit="inventory remains accurate",
        trigger="an adjustment is submitted",
        preconditions=["the manager is authenticated"],
        outcomes=["the adjustment is recorded"],
        evidence_span_ids=["span-1"],
    )
    assert render_user_story(requirement) == (
        "As a warehouse manager, I want to approve stock adjustments, "
        "so that inventory remains accurate."
    )
    assert render_gherkin(requirement) == (
        "Given the manager is authenticated, when an adjustment is submitted, "
        "then the adjustment is recorded."
    )


def test_missing_outcome_is_reported_not_invented():
    requirement = ExtractedRequirement(
        external_key="inventory-approval", category=RequirementCategory.FUNCTIONAL,
        stakeholder="warehouse manager", task="approve stock adjustments",
        evidence_span_ids=["span-1"],
    )
    assert "outcomes" in requirement.missing_fields()
    assert "<UNSPECIFIED: outcome>" in render_gherkin(requirement)
```

- [ ] **Step 2: Run the tests and verify they fail because the module is absent**

Run: `pytest agents/business_analyst/test_requirements.py -v`

- [ ] **Step 3: Implement Pydantic models and pure renderers**

Implement the exact enum values `business`, `stakeholder`, `functional`, `nonfunctional`, and `transitional`. Require non-empty `external_key` and `evidence_span_ids`; keep all semantic fields optional so ambiguity is represented explicitly. `missing_fields()` must return field names in stable lexical order. Renderers must use a deterministic placeholder, never a guessed value.

- [ ] **Step 4: Run the contract tests and verify they pass**

Run: `pytest agents/business_analyst/test_requirements.py -v`

### Task 2: Extract normalized source spans and persist structured requirements

**Files:**
- Modify: `agents/business_analyst/extraction.py`
- Modify: `agents/business_analyst/semantic_planner.py`
- Modify: `agents/business_analyst/ir.py`
- Modify: `prompt_seeds/ba_prompts.py`
- Modify: `prompt_seeds/seed.py`
- Create: `agents/business_analyst/test_requirement_extraction.py`

**Interfaces:**
- `segment_source_text(text: str) -> list[SourceSpan]` splits non-empty transcript turns in stable order and preserves character offsets.
- `build_requirement_extraction_llm(text: str) -> RequirementExtraction` fetches `ba_requirement_extraction_v1` from the database and validates structured output.
- `extract_and_persist_facts()` persists one `SourceSpan` fact, one `Requirement` fact, a `derived_from` relation, and one `Gap` fact per missing field.

- [ ] **Step 1: Write failing persistence tests**

Write an async test using the existing `db_session` and `ba_project` fixtures. Patch the extractor boundary to return one `RequirementExtraction` with one cited `SourceSpan` requirement. Assert the persisted facts have one source span, one functional requirement with category in its structured value, a `derived_from` relation to the cited span, and a `Gap` for a missing measurable NFR target. Add a second test whose extractor returns an evidence ID not present in the segment list and assert `ValueError` with no facts committed.

- [ ] **Step 2: Run the new tests and verify the expected failure**

Run: `pytest agents/business_analyst/test_requirement_extraction.py -v`

- [ ] **Step 3: Implement bounded segmentation, validated extraction, and transactional persistence**

Add `SourceSpan`/requirement Pydantic fields to the BA IR or a dedicated requirements module. The semantic planner must fetch `ba_requirement_extraction_v1` through `fetch_prompt`, call `llm_get_structured_output`, and return only schema-valid objects. Do not catch an LLM failure by generating default requirements; surface a structured extraction failure to the caller. In `extraction.py`, persist normalized source spans before requirements; validate every cited span ID belongs to the same invocation; write `Gap` facts for `missing_fields()` using the same source ID. Keep the existing `ProjectIR` extraction path intact for project-chat ingestion.

- [ ] **Step 4: Seed the versioned prompt**

Add a small prompt in `prompt_seeds/ba_prompts.py` that requires citation IDs, disallows invented values, treats source text as untrusted data, and returns the Pydantic schema. Register it in the existing seed flow. Do not add an inline fallback.

- [ ] **Step 5: Run focused tests and verify green**

Run: `pytest agents/business_analyst/test_requirement_extraction.py agents/business_analyst/test_semantic_planner.py -v`

### Task 3: Project a deterministic requirement package and agenda

**Files:**
- Create: `agents/business_analyst/capabilities/projection/requirement_package.py`
- Modify: `agents/business_analyst/capabilities/projection/__init__.py`
- Modify: `agents/business_analyst/api/routes.py`
- Create: `agents/business_analyst/test_requirement_package.py`

**Interfaces:**
- `render_requirement_package(ctx, session) -> dict[str, object]` returns requirements, stories, scenarios, gaps, agenda, and traceability.
- Route: `GET /api/ba/projects/{project_id}/requirement-package` returns this package without side effects.

- [ ] **Step 1: Write failing projection tests**

Seed append-only source-span, requirement, relation, and gap facts through `assert_fact`. Assert the package contains a user story and Gherkin created solely from stored structured fields, one agenda item per gap sorted by `requirement_key` then `field`, and a traceability row containing source span ID, requirement key, story, scenario, and gap key. Add a test that a missing outcome renders `<UNSPECIFIED: outcome>`.

- [ ] **Step 2: Run the tests and verify failure**

Run: `pytest agents/business_analyst/test_requirement_package.py -v`

- [ ] **Step 3: Implement pure fact-based projection and route**

Query facts using `get_facts(ctx, session)` only. Reconstruct requirements from `Requirement` facts; resolve `derived_from` fact relationships to source spans; group Gap facts by `value.requirement_key`; create agenda records deterministically. The endpoint must use the existing router-level JWT tenant dependency and must not create facts, call an LLM, or accept a tenant identifier from the client.

- [ ] **Step 4: Run focused projection and API tests**

Run: `pytest agents/business_analyst/test_requirement_package.py agents/business_analyst/test_api_routes.py -v`

### Task 4: Replace hardcoded derivation defaults and document runtime configuration

**Files:**
- Modify: `agents/business_analyst/capabilities/derivation/derive_requirements.py`
- Modify: `agents/business_analyst/test_capabilities.py`
- Create: `.env.example`
- Modify: `Athena.md`

**Interfaces:**
- `run_derive_requirements()` accepts only source-provided structured fields and emits gaps for absent fields.
- `.env.example` contains variable names only, including `DATABASE_URL`, LLM configuration, extraction limits, evaluation dataset, and optional object storage variables.

- [ ] **Step 1: Write a failing test for removed domain defaults**

Pass a source fact without actor, capability, object, benefit, trigger, or constraints. Assert no result contains `Nurse`, `InventoryBatch`, `HIPAA`, `view stock levels`, or `reorder_threshold`; assert a Gap fact identifies each missing field.

- [ ] **Step 2: Run the test and verify failure**

Run: `pytest agents/business_analyst/test_capabilities.py -v`

- [ ] **Step 3: Implement evidence-only derivation**

Remove every inventory and healthcare default. Read structured input fields only, emit one requirement fact only when a minimum actor/task/evidence shape exists, and emit a deterministic gap fact for each unavailable required field. Preserve the `derived_from` relation fact.

- [ ] **Step 4: Add `.env.example` with empty values only**

Use the exact variable names in Athena §8. Do not copy `.env`, use a real key, or declare provider-specific defaults in code.

- [ ] **Step 5: Run the capability tests and verify green**

Run: `pytest agents/business_analyst/test_capabilities.py -v`

### Task 5: Run the BA regression suite and execute a transcript smoke test

**Files:**
- Modify only if a failing regression demonstrates a direct violation of this plan.

- [ ] **Step 1: Run the full BA suite**

Run: `pytest agents/business_analyst -v`

- [ ] **Step 2: Execute a source-to-package smoke test**

Use the test fixtures or a small async test to ingest a transcript stating an approval flow with no time limit. Verify it produces cited requirement facts, a functional category, a user story, a Gherkin scenario, and a time-limit agenda gap. Do not call an external model; patch the structured extraction boundary with the test fixture.

- [ ] **Step 3: Verify no secret-bearing configuration is present**

Run: `python -c "from pathlib import Path; assert all(not line.split('=', 1)[1].strip() for line in Path('.env.example').read_text().splitlines() if '=' in line)"`

- [ ] **Step 4: Record test evidence in Athena**

Append the exact command and result to a `Verification` section in `Athena.md`; do not claim a command passed until it has passed.
