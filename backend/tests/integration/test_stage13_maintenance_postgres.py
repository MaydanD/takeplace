"""Maintenance observability acceptance tests on real PostgreSQL (audit FIX-02).

Retention must run independently of VK delivery and must be observable through
``/health/ops``: a deployment whose periodic maintenance has stopped has to show
up as degraded, not silently keep personal data past its retention window.
"""

from __future__ import annotations

import pytest
from app.db.time import operation_now
from app.services.maintenance_heartbeat import (
    mark_maintenance_failure,
    mark_maintenance_success,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration.test_bookings_concurrency import api_client as api_client
from tests.integration.test_bookings_concurrency import engine as engine

pytestmark = pytest.mark.integration


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _reset(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM maintenance_heartbeat"))


async def test_ops_reports_stale_when_maintenance_never_ran(
    api_client, engine, session_factory
) -> None:
    await _reset(engine)
    ops = api_client.get("/health/ops").json()
    assert ops["maintenance_last_success_age_seconds"] is None
    assert ops["maintenance_stale"] is True


async def test_ops_clears_stale_after_a_successful_pass(
    api_client, engine, session_factory
) -> None:
    await _reset(engine)
    async with session_factory() as session, session.begin():
        await mark_maintenance_success(session, now=await operation_now(session))
    ops = api_client.get("/health/ops").json()
    assert ops["maintenance_stale"] is False
    assert ops["maintenance_last_success_age_seconds"] is not None
    assert ops["maintenance_last_success_age_seconds"] < 60
    assert ops["maintenance_last_error"] is None


async def test_ops_records_the_last_failure(api_client, engine, session_factory) -> None:
    await _reset(engine)
    async with session_factory() as session, session.begin():
        await mark_maintenance_failure(
            session, now=await operation_now(session), error="OperationalError"
        )
    ops = api_client.get("/health/ops").json()
    assert ops["maintenance_last_error"] == "OperationalError"
    assert ops["maintenance_stale"] is True


async def test_ops_stays_healthy_with_a_recent_pass(api_client, engine, session_factory) -> None:
    # A recent successful pass must not, by itself, degrade a healthy database.
    await _reset(engine)
    async with session_factory() as session, session.begin():
        await mark_maintenance_success(session, now=await operation_now(session))
    ops = api_client.get("/health/ops").json()
    assert ops["maintenance_stale"] is False
