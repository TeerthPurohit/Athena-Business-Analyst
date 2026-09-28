from fastapi.testclient import TestClient

from athena.models import AthenaSource
from athena.repository import IngestedSource


class FakeRepository:
    def __init__(self):
        self.calls = []
        self.source = AthenaSource(
            id="source-1",
            org_id="org-a",
            project_id="project-a",
            content_hash="a" * 64,
            source_type="document",
        )
        self.created = True

    async def ingest(self, **kwargs):
        self.calls.append(kwargs)
        return IngestedSource(source=self.source, spans=(), created=self.created)


def test_upload_returns_original_source_for_duplicate_content():
    from athena.api import create_app

    repository = FakeRepository()
    client = TestClient(create_app(repository_factory=lambda: repository))
    path = "/v1/organizations/org-a/projects/project-a/sources"

    first = client.post(path, files={"file": ("meeting.txt", b"Approve adjustments.", "text/plain")})
    repository.created = False
    second = client.post(path, files={"file": ("copy.txt", b"Approve adjustments.", "text/plain")})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert repository.calls[0]["org_id"] == "org-a"
    assert repository.calls[0]["project_id"] == "project-a"


def test_upload_rejects_empty_content():
    from athena.api import create_app

    client = TestClient(create_app(repository_factory=FakeRepository))
    response = client.post(
        "/v1/organizations/org-a/projects/project-a/sources",
        files={"file": ("meeting.txt", b"", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty."
