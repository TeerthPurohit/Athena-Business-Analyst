"""Tests for API Routes (§11, Phase 11 + Features 1-3).

Mandatory Verification Tests:
1. Route Audit: All /api/ba routes depend on get_ba_tenant_context (or get_ba_tenant_deps), NONE on get_current_company_id.
2. JWT-only enforcement through HTTP layer: X-Organization-Id header without Bearer JWT raises 401.
3. Foreign project 404 through actual mounted FastAPI route.
4. Approval is a distinct verb: POST /approve required to approve facts or deliverables.
5. End-to-end smoke test through FastAPI TestClient.
6. New: ba_projects_router project create/list/patch, summary regenerate, clarification routes.
7. New: approve_fact bug fix — approved_by kwarg no longer raises TypeError.
"""
from datetime import datetime, timedelta, timezone
import httpx
import jwt
from fastapi import FastAPI
import pytest

from agents.business_analyst.api.routes import ba_projects_router, ba_router, get_db
from agents.business_analyst.api.security import get_ba_tenant_context
from agents.business_analyst.models import BaProject
import os
from dotenv import load_dotenv
load_dotenv()
SECRET_KEY = os.environ.get("JWT_ACCESS_SECRET", "super_secret_kaynetics_access_key_for_dev_only")

def _async_client(app: FastAPI) -> httpx.AsyncClient:
    # Not starlette's TestClient: it runs the app on its own event loop in a worker thread, but
    # db_session's asyncpg connection is bound to the test's loop, so any overridden get_db call
    # fails ("attached to a different loop"). ASGITransport runs the app in the test's own loop.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def make_api_client():
    def _client(session=None):
        app = FastAPI()
        app.include_router(ba_router)
        if session:
            async def _override_get_db():
                yield session
            app.dependency_overrides[get_db] = _override_get_db
        return _async_client(app)
    return _client


def test_route_audit_all_routes_depend_on_get_ba_tenant_context() -> None:
    """Audit: Asserts all /api/ba routes depend on get_ba_tenant_context and NONE on get_current_company_id."""
    for route in ba_router.routes:
        path = getattr(route, "path", "")
        assert path.startswith("/api/ba"), f"Route {path} missing /api/ba prefix"

        # Check route dependencies
        route_deps = getattr(route, "dependencies", [])
        endpoint = getattr(route, "endpoint", None)

        dep_names = [d.dependency.__name__ for d in route_deps if hasattr(d, "dependency")]
        if endpoint:
            # Also check parameter dependencies
            import inspect
            sig = inspect.signature(endpoint)
            for param in sig.parameters.values():
                if hasattr(param.default, "dependency"):
                    dep_names.append(param.default.dependency.__name__)

        assert "get_current_company_id" not in dep_names, f"Route {path} uses banned get_current_company_id"
        assert any("get_ba_tenant" in name for name in dep_names), f"Route {path} missing tenant context dependency"


@pytest.mark.asyncio
async def test_jwt_only_enforcement_rejects_header_override(make_api_client) -> None:
    """Asserts HTTP request with X-Organization-Id but missing Authorization Bearer JWT returns 401."""
    client = make_api_client()
    res = await client.get(
        "/api/ba/projects/p1/facts",
        headers={"X-Organization-Id": "org_header_forged"},
    )
    assert res.status_code == 401
    assert "Authorization header" in res.json()["detail"]


@pytest.mark.asyncio
async def test_foreign_project_404_through_mounted_route(db_session, make_api_client) -> None:
    """Asserts accessing foreign or non-existent project returns 404 Not Found via real DB query in route."""
    org_a = "org_a_real"
    org_b = "org_b_hacker"
    proj_id = "proj_owned_by_a"

    proj = BaProject(id=proj_id, org_id=org_a, name="Real Project A")
    db_session.add(proj)
    await db_session.flush()

    client = make_api_client(db_session)

    exp_time = datetime.now(timezone.utc) + timedelta(hours=1)
    token_b = jwt.encode({"org_id": org_b, "exp": exp_time}, SECRET_KEY, algorithm="HS256")

    res = await client.get(
        f"/api/ba/projects/{proj_id}/facts",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_end_to_end_smoke_test_semantic_planner_route(db_session, make_api_client) -> None:
    """End-to-end smoke test submitting request through semantic planner endpoint."""
    org_id = "org_smoke"
    proj_id = "proj_smoke"

    proj = BaProject(id=proj_id, org_id=org_id, name="Smoke Test Project")
    db_session.add(proj)
    await db_session.flush()

    client = make_api_client(db_session)

    exp_time = datetime.now(timezone.utc) + timedelta(hours=1)
    token = jwt.encode({"org_id": org_id, "exp": exp_time}, SECRET_KEY, algorithm="HS256")

    res = await client.post(
        f"/api/ba/planner/semantic?project_id={proj_id}",
        json={"user_request": "Develop e-commerce inventory system", "session_id": "sess_smoke"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 200
    data = res.json()
    assert "project_name" in data
    assert len(data["objectives"]) > 0


# ---------------------------------------------------------------------------
# ba_projects_router — fixtures
# ---------------------------------------------------------------------------

def make_projects_client(session=None):
    """Creates an in-loop async client mounting ba_projects_router (project-less routes)."""
    app = FastAPI()
    app.include_router(ba_projects_router)
    if session:
        async def _override_get_db():
            yield session
        app.dependency_overrides[get_db] = _override_get_db
    return app, _async_client(app)


# ---------------------------------------------------------------------------
# POST /api/ba/projects — create project
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_project_returns_201(db_session) -> None:
    """POST /api/ba/projects creates a project and returns 201 with id/settings."""
    _, client = make_projects_client(db_session)
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token = jwt.encode({"org_id": "org_create", "exp": exp}, SECRET_KEY, algorithm="HS256")

    res = await client.post(
        "/api/ba/projects",
        json={"name": "New Project", "instructions": "Test instructions"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    data = res.json()
    assert "id" in data
    assert data["name"] == "New Project"
    assert data["settings"]["instructions"] == "Test instructions"


@pytest.mark.asyncio
async def test_create_project_without_jwt_returns_401(db_session) -> None:
    """POST /api/ba/projects without JWT returns 401."""
    _, client = make_projects_client(db_session)
    res = await client.post("/api/ba/projects", json={"name": "No Auth"})
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/ba/projects — list projects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_projects_returns_only_caller_org(db_session) -> None:
    """GET /api/ba/projects returns only projects belonging to the caller's org."""
    org_a = "org_list_a"
    org_b = "org_list_b"

    proj_a = BaProject(id="proj-list-a", org_id=org_a, name="Project A")
    proj_b = BaProject(id="proj-list-b", org_id=org_b, name="Project B")
    db_session.add(proj_a)
    db_session.add(proj_b)
    await db_session.flush()

    _, client = make_projects_client(db_session)
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token_a = jwt.encode({"org_id": org_a, "exp": exp}, SECRET_KEY, algorithm="HS256")

    res = await client.get("/api/ba/projects", headers={"Authorization": f"Bearer {token_a}"})
    assert res.status_code == 200
    data = res.json()
    ids = [p["id"] for p in data]
    assert "proj-list-a" in ids
    assert "proj-list-b" not in ids


# ---------------------------------------------------------------------------
# PATCH /api/ba/projects/{project_id} — partial update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_project_merges_settings(db_session, make_api_client) -> None:
    """PATCH /api/ba/projects/{id} updates name without wiping existing settings sub-fields."""
    import uuid
    org_id = "org_patch"
    proj_id = str(uuid.uuid4())

    proj = BaProject(id=proj_id, org_id=org_id, name="Before Patch",
                     settings={"instructions": "Keep this", "must_have": "Feature X"})
    db_session.add(proj)
    await db_session.flush()

    client = make_api_client(db_session)
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token = jwt.encode({"org_id": org_id, "exp": exp}, SECRET_KEY, algorithm="HS256")

    res = await client.patch(
        f"/api/ba/projects/{proj_id}",
        json={"name": "After Patch", "should_have": "Feature Y"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "After Patch"
    # instructions and must_have preserved
    assert data["settings"]["instructions"] == "Keep this"
    assert data["settings"]["must_have"] == "Feature X"
    assert data["settings"]["should_have"] == "Feature Y"


# ---------------------------------------------------------------------------
# approve_fact bug fix — approved_by keyword no longer raises TypeError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_fact_endpoint_keyword_matches_signature(db_session, make_api_client) -> None:
    """POST approve endpoint uses asserted_by= not approved_by= — no TypeError raised."""
    from unittest.mock import AsyncMock, patch
    import uuid

    org_id = "org_bugfix"
    proj_id = str(uuid.uuid4())

    proj = BaProject(id=proj_id, org_id=org_id, name="Bug Fix Project")
    db_session.add(proj)
    await db_session.flush()

    client = make_api_client(db_session)
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token = jwt.encode({"org_id": org_id, "exp": exp}, SECRET_KEY, algorithm="HS256")

    fake_fact_id = str(uuid.uuid4())

    # Patch approve_fact to avoid needing a real fact row
    from agents.business_analyst import models as m
    from agents.business_analyst.models import BaFact
    mock_approved = BaFact(
        id=str(uuid.uuid4()),
        project_id=proj_id,
        org_id=org_id,
        subject_type="Actor",
        subject_key="test:key",
        predicate="p",
        source_id=str(uuid.uuid4()),
        asserted_by="t",
    )

    with patch("agents.business_analyst.api.routes.approve_fact", new_callable=AsyncMock) as mock_approve:
        mock_approve.return_value = mock_approved
        res = await client.post(
            f"/api/ba/projects/{proj_id}/facts/{fake_fact_id}/approve",
            json={"approved_by": "tester"},
            headers={"Authorization": f"Bearer {token}"},
        )

    # Verify the route passes asserted_by= (not approved_by=) to approve_fact
    call_kwargs = mock_approve.call_args.kwargs
    assert "asserted_by" in call_kwargs
    assert "approved_by" not in call_kwargs
    assert res.status_code == 200
