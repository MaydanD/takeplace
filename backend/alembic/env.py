"""Alembic environment.

Migrations run through the DDL-capable ``takeplace_migrator`` role
(PROJECT-SPEC §46). The DSN comes from settings, never from ``alembic.ini``.

This file is async because the runtime uses ``asyncpg``; Alembic's async
template is required for a single driver across app and migrations.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context

# Import model modules so their tables register on Base.metadata. Stage 1 has no
# domain tables yet, so autogenerate correctly produces an empty diff.
from app.db import models  # noqa: F401
from app.db.base import Base
from app.settings import get_settings
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.migrator_database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a database connection."""
    context.configure(
        url=settings.migrator_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live database using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
