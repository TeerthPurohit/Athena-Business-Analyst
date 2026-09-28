import os
import ssl
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from dotenv import load_dotenv

load_dotenv()

# ── Single source of truth: DATABASE_URL from .env ────────────────────────────
_raw_url = os.environ.get("DATABASE_URL", "")
if not _raw_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Add it to your .env file.\n"
        "Example: DATABASE_URL=postgresql+asyncpg://user:pass@host/db"
    )

# asyncpg requires the 'postgresql+asyncpg://' scheme.
# Neon and most providers hand you 'postgresql://' or 'postgres://', so we fix it.
DATABASE_URL = _raw_url
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# asyncpg rejects libpq-only query params (sslmode, channel_binding, ...).
# Strip them and pass TLS config via connect_args instead.
_connect_args: dict = {
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
}
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    _parsed = urlparse(DATABASE_URL)
    _LIBPQ_ONLY = {"sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey", "sslpassword"}
    _kept = [(k, v) for k, v in parse_qsl(_parsed.query) if k not in _LIBPQ_ONLY]
    DATABASE_URL = urlunparse(_parsed._replace(query=urlencode(_kept)))
    _connect_args["ssl"] = ssl.create_default_context()

# DATABASE_URL is a PgBouncer transaction-mode pooler endpoint (Neon), which
# multiplexes one server connection across clients. asyncpg's server-side
# prepared statements collide across that multiplexing, raising
# InvalidCachedStatementError after any schema change. Disable both caches:
# statement_cache_size (asyncpg, above) and prepared_statement_cache_size
# (SQLAlchemy asyncpg dialect, via URL param).
_parsed = urlparse(DATABASE_URL)
_q = dict(parse_qsl(_parsed.query))
_q["prepared_statement_cache_size"] = "0"
DATABASE_URL = urlunparse(_parsed._replace(query=urlencode(_q)))

# ── Async engine (single connection pool for the whole app) ───────────────────
engine = create_async_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "3")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "2")),
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db_tables():
    """Create any missing database tables automatically and seed default data."""
    from models.base import Base
    import models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # ── Auto-migrate orchestrator_tasks table to add new batch/user tracking columns ──
        from sqlalchemy import text
        dialect_name = conn.dialect.name
        if dialect_name == "postgresql":
            await conn.execute(text("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'orchestrator_tasks' AND column_name = 'user_id'
                    ) THEN
                        ALTER TABLE orchestrator_tasks ADD COLUMN user_id VARCHAR(255);
                        CREATE INDEX ix_orchestrator_tasks_user_id ON orchestrator_tasks(user_id);
                    END IF;
                    
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'orchestrator_tasks' AND column_name = 'batch_id'
                    ) THEN
                        ALTER TABLE orchestrator_tasks ADD COLUMN batch_id VARCHAR(255);
                        CREATE INDEX ix_orchestrator_tasks_batch_id ON orchestrator_tasks(batch_id);
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'orchestrator_tasks' AND column_name = 'task_name'
                    ) THEN
                        ALTER TABLE orchestrator_tasks ADD COLUMN task_name VARCHAR(255);
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'orchestrator_tasks' AND column_name = 'session_id'
                    ) THEN
                        ALTER TABLE orchestrator_tasks ADD COLUMN session_id VARCHAR(255);
                        CREATE INDEX ix_orchestrator_tasks_session_id ON orchestrator_tasks(session_id);
                    END IF;
                END $$;
            """))
        else:
            # Fallback for SQLite (local testing / dev)
            for col in ["user_id", "batch_id", "task_name", "session_id"]:
                try:
                    await conn.execute(text(f"ALTER TABLE orchestrator_tasks ADD COLUMN {col} VARCHAR(255)"))
                except Exception:
                    pass
            for col in ["user_id", "batch_id", "session_id"]:
                try:
                    await conn.execute(text(f"CREATE INDEX ix_orchestrator_tasks_{col} ON orchestrator_tasks({col})"))
                except Exception:
                    pass

    try:
        from prompt_seeds.main_agents_seeds import seed_main_agents
        await seed_main_agents()
    except Exception as e:
        import logging
        logging.getLogger("Engine").warning(f"Could not seed main_agents: {e}")

    try:
        from prompt_seeds.seed import seed_agent_prompts_async
        await seed_agent_prompts_async()
    except Exception as e:
        import logging
        logging.getLogger("Engine").warning(f"Could not seed agent_prompts: {e}")

    try:
        from prompt_seeds.agent_model_seeds import seed_agent_model_config
        await seed_agent_model_config()
    except Exception as e:
        import logging
        logging.getLogger("Engine").warning(f"Could not seed agent model config: {e}")
