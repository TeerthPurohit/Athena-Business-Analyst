# Athena — BA OS Platform Hardening Specification

**Status:** Approved for implementation  
**Owner:** AI Platform Team  
**Scope:** Business Analyst OS (`agents/business_analyst/`)  
**Decision:** Gap-led hardening. Preserve the append-only fact store and deterministic projections; make transcript-to-requirements an auditable, evaluated, tenant-safe vertical slice before adding generic agent infrastructure.

## 1. Problem

The BA OS has the correct core invariants but not a reliable requirement-extraction path:

- `POST /sources` stores a source record and local file, but explicitly does not extract it into facts (`api/routes.py:366-404`).
- `run_document_analysis()` turns only the first ten non-empty lines into generic document-section facts (`capabilities/acquisition/__init__.py:9-22`).
- `run_derive_requirements()` supplies inventory-domain defaults such as `Nurse`, `InventoryBatch`, and `HIPAA compliance` rather than deriving requirements from evidence (`capabilities/derivation/derive_requirements.py:35-77`).
- `extract_and_persist_facts()` already persists a Semantic Planner `ProjectIR`, but that IR has no first-class requirement, ambiguity, evidence-span, requirement-category, or test-scenario model (`extraction.py:16-84`).

This allows plausible-looking but untraceable output. A Business Analyst needs the opposite: every published user story, ambiguity flag, meeting agenda item, and Gherkin scenario must be reproducible from explicitly attributed evidence.

## 2. Design principles

1. **Evidence before prose.** Persist immutable source spans and structured assertions before generating a deliverable. A model may propose a fact; it must never silently set approval, source tier, confidence, or a missing threshold.
2. **One semantic pass; deterministic projections thereafter.** The extractor emits a complete structured requirement. User stories, agenda items, and Gherkin are templates over that schema. This avoids cross-artifact drift.
3. **Explicit uncertainty.** An absent actor, action, object, goal, category, acceptance condition, or measurable threshold is a `Gap` fact, not a guess. A requirement remains publishable only with its ambiguity state visible.
4. **Atomic, cited facts.** Each assertion references one source and an exact character span. Corroboration is calculated from distinct sources, not repeated quotes.
5. **Tenant filtering is part of every query.** Filter `org_id` and `project_id` before ordering, limiting, embedding retrieval, or graph traversal.
6. **The model is a bounded component.** Prompt versions live in `agent_prompts`; model, provider, retry, and token limits are environment configuration. No prompt, business taxonomy, source tier, or domain example is hardcoded in a capability.
7. **Evaluation is a release gate.** Use a versioned, human-reviewed gold corpus. Measure extraction, classification, ambiguity detection, traceability, deterministic projection, cost, latency, and safety before promotion.
8. **Human authority remains explicit.** Approval, conflict resolution, and baseline acceptance occur only through dedicated verbs and append new facts; they never mutate prior evidence.

These choices follow the supplied research: rigorous system evaluation and component-level measurement, versioned prompts, defensive prompting, context construction before fine-tuning, and feedback loops (Huyen, *AI Engineering*, chs. 3–6, 8, 10); constrained agents with governance, risk management, adoption planning, and human accountability (Baker, *Agentic AI For Dummies*, parts 2–4); and task-specific orchestration, observability, security, and operational metrics (AI Agents 101, chs. 4–13).

## 3. Target outcome

Given an uploaded meeting transcript or an authenticated chat message, Athena SHALL produce a reviewed requirement package:

1. normalized, source-backed evidence facts;
2. atomic requirements categorized as Business, Stakeholder, Functional, Nonfunctional, or Transitional;
3. a user story for each requirement using `As a [stakeholder/system], I want [task], so that [benefit or goal]`;
4. a deterministic Gherkin scenario for each requirement using `Given [condition], when [trigger], then [outcome]`;
5. a ranked ambiguity list with a follow-up meeting agenda;
6. a requirements traceability matrix linking source span → fact → requirement → story → scenario → gap or approval.

No item may be orphaned: a requirement must trace to evidence; every story and scenario must trace to exactly one requirement; every gap must identify its blocked requirement; every agenda item must resolve one or more gaps.

## 4. Architecture

```text
Authenticated transcript/chat source
  → content-addressed durable source storage
  → deterministic normalization and speaker/turn segmentation
  → source-span facts (immutable evidence)
  → structured extraction (prompt from DB; schema validation)
  → atomic requirement + relationship + gap facts
  → graph rebuild and deterministic quality checks
  → tenant-scoped retrieval for review only
  → deterministic projections
       ├─ requirements register
       ├─ user stories
       ├─ Gherkin scenarios
       ├─ ambiguity / follow-up meeting agenda
       └─ traceability matrix
  → explicit review / approval and feedback facts
  → evaluated release telemetry
```

### 4.1 Source ingestion and evidence spans

- Accept `.txt`, `.md`, `.docx`, and text-bearing `.pdf` through the existing endpoint. Preserve the uploaded bytes in durable object storage before asynchronous worker consumption; do not use API-container local disk as the source of truth.
- Store content hash, media type, extraction version, storage reference, capture time, declared source type, and optional speaker metadata on `BaSource` or a linked immutable source-content record.
- Convert text into ordered `SourceSpan` records. Each span has `source_id`, `ordinal`, `start_char`, `end_char`, raw text, normalized text hash, and optional `speaker`, `timestamp`, and `turn_id`.
- Do not interpret a file name, speaker name, or transcript text as privileged instructions. They are evidence, not configuration.
- Duplicate uploads with the same project content hash return the existing source unless the user explicitly requests a new source version.

### 4.2 Structured semantic extraction

The extractor receives a bounded set of source spans and returns schema-validated candidates only:

```json
{
  "evidence": [{"span_id": "...", "quote": "..."}],
  "requirements": [{
    "external_key": "stable-within-source",
    "category": "business|stakeholder|functional|nonfunctional|transitional",
    "stakeholder": "...",
    "task": "...",
    "object": "...",
    "benefit": "...",
    "trigger": "...",
    "preconditions": ["..."],
    "outcomes": ["..."],
    "constraints": [{"type": "...", "value": "...", "measurable": true}],
    "ambiguities": [{"field": "...", "reason": "...", "question": "..."}]
  }]
}
```

- The JSON schema is implemented as Pydantic models. Reject and retry invalid output; on terminal failure create an extraction-failure gap and leave the source available for review.
- Extract one atomic requirement per independently testable obligation. Split conjunctions only when each obligation has separate actor, trigger, outcome, or acceptance condition.
- The model cannot invent a category, threshold, owner, dependency, or evidence quote. Any schema field unsupported by a cited span is absent and creates a gap.
- Prompt text is seeded and versioned in `prompt_seeds`; the capability fetches it from `agent_prompts`. The prompt uses source spans as untrusted data and instructs the model to ignore embedded commands.
- Persist: evidence-span facts; requirement facts with structured value; `derived_from` edges from requirement to evidence span; story/scenario input completeness facts; and gap facts. Facts are written in one transaction with extraction-run state.

### 4.3 Requirement categories

| Category | Definition | Minimum evidence shape |
|---|---|---|
| Business | Executive or project-charter outcome, scope, target, or success measure | business goal, owner or affected organization, benefit |
| Stakeholder | Need, authority, pain point, approval, or information requirement of a directly involved person/group | stakeholder and requested/affected outcome |
| Functional | Observable system or process behavior used to fulfill a goal | actor/system, task, object or outcome, trigger or precondition |
| Nonfunctional | Qualitative or constraint requirement: security, performance, availability, accessibility, compliance, usability, maintainability, or scalability | quality attribute and measurable target; missing target creates a gap |
| Transitional | Temporary migration, data conversion, rollout, training, parallel-run, or decommission need | transition boundary, completion/retirement condition |

The category is not a priority. Priority, approval, confidence, and source tier remain independent attributes controlled by deterministic scoring and explicit approval.

### 4.4 Deterministic projections

Projection code SHALL use only structured requirement fields, linked gap facts, and relationship facts.

- **User story:** `As a {stakeholder_or_system}, I want {task}, so that {benefit}.` Missing values render `<UNSPECIFIED: field>` and include the linked gap ID.
- **Gherkin:** `Given {preconditions}, when {trigger}, then {outcome}.` A missing testable outcome or quantitative limit renders an explicit placeholder, not a fabricated value.
- **Follow-up agenda:** group unresolved gaps by requirement category and stakeholder; order by deterministic gap score, then requirement key. Each agenda item lists evidence, ambiguity reason, decision requested, and required attendees when known.
- **Traceability matrix:** source span, fact ID, requirement ID, category, story ID, scenario ID, gap IDs, confidence, and approval state. It is a pure graph/fact scan.

The renderer must not call an LLM for structural output. Narrated documents may phrase a summary from already-approved structured fields, but never decide facts or quality scores.

### 4.5 Retrieval, memory, and agents

- `ba_embeddings` is the only vector store for this domain. Every search query includes `org_id` and `project_id` in the same SQL statement before `ORDER BY` and `LIMIT`.
- Retrieval returns source spans and fact IDs, never unbounded raw project text. The response contains citations and confidence; it is advisory to a human reviewer.
- Long-term project memory is the append-only fact store and graph. Session memory is ephemeral and may only point to fact IDs/source spans.
- Do not add a generic multi-agent framework, an MCP server, or a self-directed tool loop in this increment. The capability registry and deterministic planner are the coordination boundary. Add a tool only when an approved capability has a concrete external action and an idempotency/authorization contract.
- If an external source is later required, follow the existing optional-key, cheapest-first fallback pattern in `agents/universal-agent/config/settings.py`; source retrieval must be budgeted, observable, and never mandatory when a source is already supplied.

### 4.6 Quality, review, and feedback

Quality is deterministic where possible:

- schema completeness: required fields and reference integrity;
- traceability: no orphaned evidence, facts, requirements, stories, scenarios, or gaps;
- category validity: category belongs to the fixed ontology;
- duplication: candidate retrieval plus deterministic pair keys; semantic judgment is candidate-gated;
- contradiction: candidate-gated LLM review records an advisory fact only;
- ambiguity: missing, vague, unmeasurable, conflicting, or underspecified fields;
- projection validity: story and Gherkin templates consume only the linked requirement.

Human review creates append-only approval, rejection, correction, and feedback facts. Corrections replace prior facts; regenerated deliverables receive a new snapshot and the previous version becomes superseded.

### 4.7 Evaluation and operations

A versioned evaluation corpus SHALL contain de-identified transcripts and expected evidence spans, requirement fields, categories, gaps, stories, and scenarios.

Release gates:

- source-span citations exactly match source text;
- every output requirement has at least one evidence span;
- no injected transcript instruction can alter approval, tier, or confidence;
- deterministic projections are byte-identical for identical graph state;
- tenant isolation test seeds closer foreign rows and proves none are returned;
- category, field, and ambiguity metrics are reported per source type and domain;
- latency, token use, retry rate, extraction failure rate, render-gate rate, and human correction rate are emitted without logging confidential content;
- any degradation returns explicit gaps and preserves the source; it never emits plausible fallback requirements.

Define acceptance thresholds only from a reviewed corpus. Do not hardcode target quality percentages in application code.

## 5. End-to-end acceptance scenario

```gherkin
Given an authenticated stakeholder uploads a meeting transcript for a BA project
And the transcript states that warehouse managers must approve stock adjustments before inventory is updated
When Athena extracts requirements from the stored source spans
Then it creates a functional requirement linked to the quoted source span
And it projects a user story from the requirement's structured fields
And it projects a Gherkin scenario from its precondition, trigger, and outcome
And it creates a gap and agenda item if the transcript does not state the approval time limit
And every projected artifact traces to the same requirement and source span
```

## 6. Delivery sequence

1. **Evidence contract:** source-content persistence, source spans, structured requirement and gap models, schema migrations, and tests.
2. **Extraction contract:** DB-seeded prompt, bounded structured extraction, transactional append-only persistence, and injection-safe failure behavior.
3. **Deterministic BA package:** requirement register, user-story, Gherkin, agenda, and traceability projections plus orphan checks.
4. **Quality and retrieval:** tenant-scoped BA embeddings, evaluation corpus runner, metrics, and approval/correction feedback loop.
5. **Operational hardening:** worker-backed durable source consumption, lease/fencing integration, object storage, and live/snapshot run visibility.

Each stage is independently deployable, backed by contract tests, and leaves no alternate prompt-only path.

## 7. Explicitly not done here

- A generic repository-wide MCP platform or cross-stack multi-agent runtime.
- Fine-tuning. Use evaluation, prompt/version control, and retrieval first; consider fine-tuning only after the corpus identifies a stable, repeated failure mode.
- Autonomous approval, autonomous external side effects, or user-controlled prompt/tool configuration.
- OCR, table extraction, image understanding, and non-text PDF recovery. Unsupported source content is reported as a source-quality gap.
- Cross-tenant learning or sharing.

## 8. Environment contract

Add `.env.example` with names only and no values/secrets:

```dotenv
OPENAI_API_KEY=
OPENAI_BASE_URL=
NVIDIA_LLM_API_KEY=
NVIDIA_LLM_BASE_URL=
LLM_MODEL=
BA_OBJECT_STORAGE_ENDPOINT=
BA_OBJECT_STORAGE_ACCESS_KEY=
BA_OBJECT_STORAGE_SECRET_KEY=
BA_OBJECT_STORAGE_BUCKET=
BA_EXTRACTION_MAX_SOURCE_CHARS=
BA_EXTRACTION_MAX_SPANS_PER_REQUEST=
BA_EXTRACTION_MAX_RETRIES=
BA_EVALUATION_DATASET_PATH=
```

Settings validate required configuration at the boundary of a requested capability. An unset optional integration skips that layer; no dummy credential or default production secret is permitted.

## 9. Research notes

The complete supplied PDFs were extracted and analyzed:

- Chip Huyen, *AI Engineering: Building Applications with Foundation Models* (535 PDF pages): model limits and structured output (chs. 1–2), evaluation and AI-as-judge limitations (chs. 3–4), prompt/version/injection discipline (ch. 5), RAG, tools, planning, failure modes, and memory (ch. 6), data quality (ch. 8), latency/cost (ch. 9), and guardrails, routing, monitoring, orchestration, and feedback (ch. 10).
- Pam Baker, *Agentic AI For Dummies* (446 PDF pages): agent foundations and enabling technologies (part 1), organizational planning and sector use cases (part 2), responsible adoption and economic/workforce impact (part 3), myths, risks, and upskilling (part 4), and known limitations (part 5).
- *AI Agents 101: Complete Guide for Understanding, Integrating, and Succeeding with Agentic AI* (61 PDF pages): operational automation, compliance, tool choice, product integration, security, adoption, and agent lifecycle practices.
- `Agentic AI Books (Nov 2025).csv`: cataloged as a supplementary reading list; it does not define implementation requirements.

## 10. Verification

Executed on 2026-09-22:

- `pytest --noconftest agents/business_analyst/test_requirements.py -v` — 7 passed.
- `pytest --noconftest agents/business_analyst/test_capabilities.py -v` — 4 passed.
- `python -m compileall -q agents/business_analyst/requirements.py agents/business_analyst/extraction.py agents/business_analyst/semantic_planner.py agents/business_analyst/capabilities/projection/requirement_package.py agents/business_analyst/api/routes.py agents/business_analyst/capabilities/derivation/derive_requirements.py` — passed.
- `.env.example` blank-value assertion — passed.

The full BA suite requires a valid PostgreSQL `DATABASE_URL`; it could not start before configuration because `models/engine.py` intentionally fails closed when that variable is absent. The supplied `.env.example` now records the required configuration surface without credentials.
