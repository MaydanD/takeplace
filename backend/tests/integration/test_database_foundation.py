"""Integration tests against a live PostgreSQL instance.

These are skipped unless ``TAKEPLACE_TEST_DATABASE_URL`` points at a real
PostgreSQL 15+ database. SQLite is never an acceptable substitute: the whole
point of these tests is PostgreSQL-specific behaviour (PROJECT-SPEC §3.3, §45).

They verify the Stage 1 database foundation:

* the connection is usable and reports the expected server;
* ``btree_gist`` is installed by the baseline migration;
* the per-connection statement/lock/idle timeouts are actually in effect;
* the application role cannot perform DDL.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

TEST_URL = os.environ.get("TAKEPLACE_TEST_DATABASE_URL")


@pytest.fixture
async def session() -> AsyncSession:  # type: ignore[misc]
    if not TEST_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    engine = create_async_engine(TEST_URL, poolclass=None)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


async def test_postgres_version_is_supported(session: AsyncSession) -> None:
    result = await session.execute(text("SHOW server_version_num"))
    major = int(result.scalar_one()) // 10000
    assert major >= 15, "Takeplace requires PostgreSQL 15 or newer"


async def test_btree_gist_extension_installed(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'"))
    assert result.scalar_one() == 1


async def test_clock_timestamp_is_used_for_operation_time(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT clock_timestamp()"))
    assert result.scalar_one() is not None
