"""FastAPI application factory for Athena."""

from collections.abc import Callable
from typing import Annotated, Protocol

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from athena.repository import IngestedSource


class SourceRepository(Protocol):
    async def ingest(
        self,
        *,
        org_id: str,
        project_id: str,
        filename: str,
        content: bytes,
        media_type: str,
    ) -> IngestedSource: ...


def create_app(*, repository_factory: Callable[[], SourceRepository]) -> FastAPI:
    """Build Athena's HTTP surface with a request-safe repository provider."""
    app = FastAPI(title="Athena")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/organizations/{org_id}/projects/{project_id}/sources")
    async def upload_source(
        org_id: str,
        project_id: str,
        file: Annotated[UploadFile, File(...)],
    ) -> JSONResponse:
        content = await file.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )
        result = await repository_factory().ingest(
            org_id=org_id,
            project_id=project_id,
            filename=file.filename or "upload",
            content=content,
            media_type=file.content_type or "application/octet-stream",
        )
        return JSONResponse(
            status_code=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
            content={
                "id": result.source.id,
                "content_hash": result.source.content_hash,
                "span_count": len(result.spans),
            },
        )

    return app
