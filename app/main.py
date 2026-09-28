"""Athena business analyst agent application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agents.business_analyst.api.routes import ba_projects_router, ba_router, ba_ws_router
from agents.business_analyst.api.auth import auth_router

logger = logging.getLogger("ba_app")


async def _initialize_database() -> None:
    """Install BA schema and seed the catalog without loading former agent stacks."""
    from agents.business_analyst.models import Base as ba_base
    from models.agent_prompt import AgentPrompt  # noqa: F401 - registers the table
    from models.base import Base as shared_base
    from models.engine import engine

    # Agent prompts are retained because the BA semantic planner reads its prompt from DB.
    async with engine.begin() as connection:
        await connection.run_sync(shared_base.metadata.create_all)
        await connection.run_sync(ba_base.metadata.create_all)

    from prompt_seeds.seed import seed_ba_prompts_async

    await seed_ba_prompts_async()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await _initialize_database()
    except Exception:
        logger.exception("BA database initialization failed; API will remain available for diagnosis")
    yield
    from models.engine import engine

    await engine.dispose()


app = FastAPI(
    title="Athena",
    description="Evidence-backed business analysis, fact storage, planning, and deliverables.",
    version="1.0.0",
    lifespan=lifespan,
)

configured_cors_origins = os.getenv("CORS_ORIGINS", "")
cors_origins = [
    origin.strip().rstrip("/")
    for origin in configured_cors_origins.split(",")
    if origin.strip()
]
if not cors_origins:
    cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ba_projects_router)
app.include_router(auth_router)
app.include_router(ba_router)
app.include_router(ba_ws_router)

frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/app", StaticFiles(directory=frontend_dist, html=True), name="athena-workspace")


@app.get("/", tags=["System"])
async def root() -> dict[str, str]:
    return {"service": "Athena", "status": "online", "docs": "/docs"}


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080)
