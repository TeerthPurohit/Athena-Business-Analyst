from fastapi.testclient import TestClient


def test_configured_app_serves_health_without_connecting(monkeypatch):
    from athena.main import create_configured_app

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@host.example/db")
    monkeypatch.setenv("BA_OBJECT_STORAGE_ENDPOINT", "https://storage.example.neon.tech")
    monkeypatch.setenv("BA_OBJECT_STORAGE_ACCESS_KEY", "token")
    monkeypatch.setenv("BA_OBJECT_STORAGE_SECRET_KEY", "secret")
    monkeypatch.setenv("BA_OBJECT_STORAGE_BUCKET", "athena-sources")
    monkeypatch.setenv("BA_OBJECT_STORAGE_REGION", "ap-southeast-1")
    monkeypatch.setenv("BA_OBJECT_STORAGE_FORCE_PATH_STYLE", "true")

    response = TestClient(create_configured_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
