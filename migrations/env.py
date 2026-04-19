"""
Alembic environment configuration for async SQLAlchemy.

Uses asyncio runner so migrations can run with the asyncpg driver.
Models are imported via app.models to enable autogenerate.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# ── Import models so Alembic can discover tables via autogenerate ──────────────
from app.db.base import Base
import app.models  # noqa: F401 — side-effect: registers all model metadata

from app.core.config import settings

# Alembic config object
config = context.config

# Configure stdlib logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata Alembic compares against to generate diffs
target_metadata = Base.metadata


def _runtime_database_url() -> str:
    """
    Prefer raw process env on Render pre-deploy so Alembic does not accidentally
    fall back to local development defaults from .env templates.
    """
    render_db = (os.environ.get("RENDER_DATABASE_URL") or "").strip()
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    return render_db or database_url or settings.database_url


def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection.
    Useful for generating SQL scripts.
    """
    url = _runtime_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine (required by asyncpg)."""
    engine = create_async_engine(_runtime_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
