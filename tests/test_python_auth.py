"""Athena account flow using an isolated in-memory database."""

import asyncio

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agents.business_analyst.api.auth import auth_router, get_auth_db
from agents.business_analyst.models.user import BaUser


def test_register_login_and_org_isolation(monkeypatch):
    monkeypatch.setenv("JWT_ACCESS_SECRET", "test-secret-that-is-long-enough-for-hs256")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(BaUser.__table__.create)

    asyncio.run(create_schema())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_db():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_auth_db] = test_db
    with TestClient(app) as client:
        first = client.post("/api/auth/register", json={
            "name": "Ada", "email": "ADA@example.com", "password": "a-long-test-password",
        })
        assert first.status_code == 201, first.text
        first_data = first.json()
        claims = jwt.decode(first_data["access_token"], "test-secret-that-is-long-enough-for-hs256", algorithms=["HS256"])
        assert claims["org_id"] == first_data["user"]["org_id"]
        assert claims["sub"] == first_data["user"]["id"]
        assert claims["exp"] > claims["iat"]
        assert first_data["user"]["email"] == "ada@example.com"

        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {first_data['access_token']}"}).json()["name"] == "Ada"
        assert client.post("/api/auth/login", json={"email": "ada@example.com", "password": "wrong"}).status_code == 401
        assert client.post("/api/auth/login", json={"email": "ADA@example.com", "password": "a-long-test-password"}).status_code == 200
        assert client.post("/api/auth/register", json={"name": "Ada", "email": "ada@example.com", "password": "a-long-test-password"}).status_code == 409
        assert client.post("/api/auth/register", json={"name": "Short", "email": "short@example.com", "password": "short"}).status_code == 422
        second = client.post("/api/auth/register", json={
            "name": "Grace", "email": "grace@example.com", "password": "another-long-password",
        })
        assert second.status_code == 201
        assert second.json()["user"]["org_id"] != first_data["user"]["org_id"]
    asyncio.run(engine.dispose())
