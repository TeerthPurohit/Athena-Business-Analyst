# Athena Standalone Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish Athena as a standalone, migratable Python service whose first deployable vertical slice accepts and persists tenant-scoped source evidence without touching legacy BA code.

**Architecture:** The root `athena/` package owns its FastAPI application, Pydantic settings, SQLAlchemy metadata, and Alembic history. Source content is content-addressed and stored through a single object-store boundary; the relational database holds source metadata and immutable normalized spans. This plan implements the evidence foundation only; structured extraction and projections follow once the evidence contract is live.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 async ORM, Alembic, PostgreSQL/asyncpg, S3-compatible object storage, pytest.

## Global Constraints

- Athena code lives only under `athena/`, root `alembic/`, and root configuration/test files; do not import or change `agents/business_analyst/`.
- Every persisted source, span, and query is constrained by both `org_id` and `project_id`.
- Uploaded content, filenames, and metadata are evidence, never instructions.
- Source bytes are stored before later worker consumption; API-local disk is never a source of truth.
- The fact store, extraction prompt, LLM provider, review workflow, and projections are out of this first foundation milestone.
- Required environment values are validated at the capability boundary; `.env.example` contains names only and no values.
- No generic agent framework, MCP server, OCR, or default production credential is added.

---

### Task 1: Bootstrap the standalone Athena service and schema lineage

**Files:**
- Create: `athena/__init__.py`
- Create: `athena/api.py`
- Create: `athena/config.py`
- Create: `athena/db.py`
- Create: `athena/models.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_athena_source_evidence.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Test: `tests/athena/test_config.py`
- Test: `tests/athena/test_models.py`

**Interfaces:**
- Produces `create_app() -> FastAPI`, `Settings`, `Base`, `AthenaSource`, `AthenaSourceContent`, and `AthenaSourceSpan`.
- `AthenaSource` has UUID id, org/project IDs, SHA-256 `content_hash`, declared source type, capture time, and unique `(org_id, project_id, content_hash)`.
- `AthenaSourceContent` is one-to-one with a source and contains immutable storage reference, media type, extraction version, and capture time.
- `AthenaSourceSpan` has a stable UUID id, source/tenant/project identity, ordinal, character offsets, raw text, normalized text hash, and optional speaker/timestamp/turn identifier.

- [ ] **Step 1: Write failing import and metadata tests**

```python
def test_standalone_models_expose_evidence_tables():
    from athena.models import Base

    assert {"athena_source", "athena_source_content", "athena_source_span"} <= set(Base.metadata.tables)


def test_settings_require_database_only_when_database_capability_requested(monkeypatch):
    from athena.config import Settings

    settings = Settings()
    assert settings.database_url is None
    with pytest.raises(ValueError, match="DATABASE_URL"):
        settings.require_database()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest --noconftest tests/athena/test_config.py tests/athena/test_models.py -v`

Expected: FAIL because the standalone package does not exist.

- [ ] **Step 3: Implement the minimum independent service foundation**

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    object_storage_endpoint: str | None = Field(default=None, validation_alias="BA_OBJECT_STORAGE_ENDPOINT")

    def require_database(self) -> str:
        if not self.database_url:
            raise ValueError("DATABASE_URL must be configured for this capability")
        return self.database_url
```

Define SQLAlchemy models and the initial Alembic revision in the standalone lineage. The revision creates all three Athena tables, source/hash and source/ordinal constraints, and tenant/project query indexes. It never alters legacy tables.

- [ ] **Step 4: Run the foundation tests to verify they pass**

Run: `python -m pytest --noconftest tests/athena/test_config.py tests/athena/test_models.py -v`

Expected: PASS.

### Task 2: Implement durable, content-addressed source ingestion

**Files:**
- Create: `athena/storage.py`
- Create: `athena/ingestion.py`
- Create: `tests/athena/test_ingestion.py`
- Modify: `athena/models.py`

**Interfaces:**
- Consumes `org_id`, `project_id`, source bytes, filename, media type, and an `ObjectStore`.
- Produces `IngestedSource(source, spans, created)`.
- `ObjectStore.put_if_absent(key: str, content: bytes, media_type: str) -> str` writes only a SHA-256-derived key and returns an opaque durable reference.

- [ ] **Step 1: Write failing pure ingestion tests**

```python
def test_ingest_builds_stable_spans_and_stores_content_once():
    store = FakeObjectStore()
    result = ingest_text_source(
        org_id="org-a", project_id="project-a", filename="meeting.txt",
        content=b"Manager: approve adjustments.\nInventory updates after approval.", store=store,
    )

    assert result.storage_key == f"sha256/{result.content_hash}"
    assert [span.raw_text for span in result.spans] == [
        "Manager: approve adjustments.", "Inventory updates after approval.",
    ]
    assert store.writes == 1
```

- [ ] **Step 2: Run the ingestion test to verify it fails**

Run: `python -m pytest --noconftest tests/athena/test_ingestion.py -v`

Expected: FAIL because ingestion and the object-store boundary do not exist.

- [ ] **Step 3: Implement deterministic byte hashing, storage, and segmentation**

```python
@dataclass(frozen=True)
class SourceSpanDraft:
    ordinal: int
    start_char: int
    end_char: int
    raw_text: str
    normalized_text_hash: str


def ingest_text_source(*, content: bytes, store: ObjectStore, ...) -> IngestedSourceDraft:
    content_hash = hashlib.sha256(content).hexdigest()
    storage_key = f"sha256/{content_hash}"
    storage_ref = store.put_if_absent(storage_key, content, media_type)
    return IngestedSourceDraft(content_hash=content_hash, storage_ref=storage_ref,
                               spans=segment_text(content.decode("utf-8")))
```

Use a production S3-compatible implementation configured only when object-storage settings are supplied, plus a fake store in tests. Do not use local filesystem fallback.

- [ ] **Step 4: Run the ingestion tests to verify they pass**

Run: `python -m pytest --noconftest tests/athena/test_ingestion.py -v`

Expected: PASS.

### Task 3: Persist and expose the source-ingestion boundary

**Files:**
- Create: `athena/repository.py`
- Modify: `athena/api.py`
- Modify: `athena/db.py`
- Create: `tests/athena/test_api.py`

**Interfaces:**
- Consumes `POST /v1/organizations/{org_id}/projects/{project_id}/sources` multipart upload.
- Produces HTTP 201 for a new source and HTTP 200 with the original source ID for duplicate bytes in the same tenant/project.
- `AthenaRepository.ingest()` queries `(org_id, project_id, content_hash)` before creating relational records and commits source/content/spans atomically after durable content storage succeeds.

- [ ] **Step 1: Write failing API-contract tests**

```python
def test_upload_returns_existing_source_for_same_project_content(client, fake_store):
    first = client.post(SOURCE_PATH, files={"file": ("meeting.txt", b"Approve adjustments.", "text/plain")})
    second = client.post(SOURCE_PATH, files={"file": ("copy.txt", b"Approve adjustments.", "text/plain")})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
```

- [ ] **Step 2: Run API tests to verify they fail**

Run: `python -m pytest --noconftest tests/athena/test_api.py -v`

Expected: FAIL because the route and repository do not exist.

- [ ] **Step 3: Implement the narrow tenant-scoped upload route**

```python
@app.post("/v1/organizations/{org_id}/projects/{project_id}/sources")
async def upload_source(org_id: UUID, project_id: UUID, file: UploadFile, repository: RepositoryDep):
    result = await repository.ingest(org_id=org_id, project_id=project_id,
                                     filename=file.filename or "upload", content=await file.read(),
                                     media_type=file.content_type or "application/octet-stream")
    return JSONResponse(status_code=201 if result.created else 200, content=result.response())
```

Reject empty uploads. Restrict text extraction to `.txt`, `.md`, `.docx`, and text-bearing `.pdf`; return a source-quality state for unsupported or unextractable content without attempting OCR. Never return source bytes in the response.

- [ ] **Step 4: Run the API contract tests to verify they pass**

Run: `python -m pytest --noconftest tests/athena/test_api.py -v`

Expected: PASS.

### Task 4: Run the standalone evidence smoke suite

**Files:**
- Test: `tests/athena/test_config.py`
- Test: `tests/athena/test_models.py`
- Test: `tests/athena/test_ingestion.py`
- Test: `tests/athena/test_api.py`

- [ ] **Step 1: Run the milestone suite**

Run: `python -m pytest --noconftest tests/athena -v`

Expected: PASS.

- [ ] **Step 2: Run the source smoke check**

Run: `python -m compileall -q athena`

Expected: exit code 0.
