"""Background worker loop tests (PROJECT-SPEC §38.3, §42.2, audit FIX-02).

These exercise the *control flow* of :class:`app.worker.VKOutboxWorker` without a
database: maintenance and VK delivery are independent loops, and a failure in one
must never stop the other or busy-loop. The durable outcome of a pass is covered
by the PostgreSQL integration tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from app.settings import Settings
from app.worker import VKOutboxWorker


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "development",
        # Port 1 refuses instantly: no test ever opens a real connection.
        "database_url": "postgresql+asyncpg://u:p@127.0.0.1:1/db",
        "realtime_listener_enabled": False,
        "idempotency_hmac_key": "test-idempotency-key-0123456789",
        "abuse_hmac_key": "test-abuse-key-0123456789",
        "vk_encryption_keys": "1:" + "A" * 43,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _noop(*_args: object, **_kwargs: object) -> None:
    return None


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not met before the deadline")
        await asyncio.sleep(0.01)


async def test_maintenance_runs_when_vk_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Retention must not depend on the VK feature flag (audit FIX-02).
    worker = VKOutboxWorker(_settings(vk_worker_enabled=False, maintenance_interval_seconds=60))
    calls = {"maintenance": 0, "delivery": 0}

    async def fake_maintenance() -> None:
        calls["maintenance"] += 1

    async def fake_delivery() -> int:
        calls["delivery"] += 1
        raise AssertionError("delivery must not run while VK is disabled")

    monkeypatch.setattr(worker, "_maintenance_once", fake_maintenance)
    monkeypatch.setattr(worker, "_run_once", fake_delivery)
    monkeypatch.setattr(worker, "_beat", _noop)

    task = asyncio.create_task(worker.run())
    await _wait_until(lambda: calls["maintenance"] >= 1)
    worker.request_stop()
    await asyncio.wait_for(task, 2)
    assert calls["maintenance"] >= 1
    assert calls["delivery"] == 0


async def test_delivery_error_does_not_stop_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = VKOutboxWorker(
        _settings(
            vk_worker_enabled=True,
            vk_worker_poll_interval_seconds=0.01,
            maintenance_interval_seconds=60,
        )
    )
    counts = {"maintenance": 0, "cycles": 0}

    async def exploding_cycle() -> int:
        counts["cycles"] += 1
        raise RuntimeError("vk/db outage")

    async def fake_maintenance() -> None:
        counts["maintenance"] += 1

    monkeypatch.setattr(worker, "_run_once", exploding_cycle)
    monkeypatch.setattr(worker, "_maintenance_once", fake_maintenance)
    monkeypatch.setattr(worker, "_beat", _noop)

    task = asyncio.create_task(worker.run())
    await _wait_until(lambda: counts["maintenance"] >= 1 and counts["cycles"] >= 3)
    worker.request_stop()
    await asyncio.wait_for(task, 2)
    assert counts["maintenance"] >= 1


async def test_failed_maintenance_pass_does_not_busy_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = VKOutboxWorker(_settings(vk_worker_enabled=False, maintenance_interval_seconds=60))
    calls = {"maintenance": 0}

    async def failing_maintenance(*_args: object, **_kwargs: object) -> None:
        calls["maintenance"] += 1
        raise RuntimeError("retention database is down")

    async def fake_clock(_session: object) -> datetime:
        return datetime.now(UTC)

    # The pass body is patched (app.worker imported it by name) and the clock is
    # faked, so the real ``_maintenance_once`` reaches the failing call, catches
    # it and then waits one full interval. A retry storm would be thousands.
    monkeypatch.setattr("app.worker.run_maintenance", failing_maintenance)
    monkeypatch.setattr("app.worker.operation_now", fake_clock)
    monkeypatch.setattr(worker, "_beat", _noop)

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.25)
    worker.request_stop()
    await asyncio.wait_for(task, 2)
    assert calls["maintenance"] == 1
