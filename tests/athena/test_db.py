from athena.db import normalize_async_database_url


def test_normalize_neon_postgres_url_for_asyncpg():
    normalized = normalize_async_database_url(
        "postgresql://user:password@host.example/db?sslmode=require&channel_binding=require"
    )

    assert normalized.startswith("postgresql+asyncpg://user:password@host.example/db?")
    assert "sslmode" not in normalized
    assert "channel_binding" not in normalized
    assert "prepared_statement_cache_size=0" in normalized
