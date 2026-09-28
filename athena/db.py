"""Async database session factory for Athena."""

import ssl
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_LIBPQ_ONLY_PARAMETERS = {
    "sslmode",
    "channel_binding",
    "sslrootcert",
    "sslcert",
    "sslkey",
    "sslpassword",
}


def normalize_async_database_url(database_url: str) -> str:
    """Convert standard Neon URLs into asyncpg-safe SQLAlchemy URLs."""
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(database_url)
    query = {
        key: value
        for key, value in parse_qsl(parsed.query)
        if key not in _LIBPQ_ONLY_PARAMETERS
    }
    query["prepared_statement_cache_size"] = "0"
    return urlunparse(parsed._replace(query=urlencode(query)))


def create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Build a Neon-safe session factory only after database access is requested."""
    normalized_url = normalize_async_database_url(database_url)
    engine = create_async_engine(
        normalized_url,
        connect_args={
            "ssl": ssl.create_default_context(),
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
