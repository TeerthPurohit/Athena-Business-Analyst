from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from athena.models import Base
from agents.business_analyst.models import Base as ba_base

config = context.config

import os
from dotenv import load_dotenv
load_dotenv()

_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    if _db_url.startswith("postgresql+asyncpg://"):
        _db_url = _db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    config.set_main_option("sqlalchemy.url", _db_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        # BA tables are created by the application with create_all; ensure a
        # fresh database has ba_fact before the trigger migration runs.
        ba_base.metadata.create_all(connection)
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
