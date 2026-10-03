"""Async database engine and session management.

The engine applies the PostgreSQL timeouts from PROJECT-SPEC §32.6 to every
connection. They are set both as asyncpg ``server_settings`` (so the server
enforces them) and as SQLAlchemy pool configuration. ``READ COMMITTED`` is the
v1 isolation level (§32.0); correctness comes from explicit row locks,
exclusion/unique constraints and advisory guards, not from a higher isolation
level.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def build_engine(settings: Settings, *, url: str | None = None) -> AsyncEngine:
    """Create an async engine with the canonical timeout settings."""
    server_settings = {
        "statement_timeout": str(settings.db_statement_timeout_ms),
        "lock_timeout": str(settings.db_lock_timeout_ms),
        "idle_in_transaction_session_timeout": str(settings.db_idle_in_transaction_timeout_ms),
        "default_transaction_isolation": "read committed",
        # Deterministic timezone for diagnostics; business time is venue-local.
        "timezone": "UTC",
    }
    return create_async_engine(
        url or settings.app_database_url,
        echo=False,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"server_settings": server_settings},
    )


def init_engine(settings: Settings) -> AsyncEngine:
    """Initialise the process-wide engine and session factory."""
    global _engine, _session_factory
    if _engine is None:
        _engine = build_engine(settings)
        _session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine


async def dispose_engine() -> None:
    """Dispose the process-wide engine and reset the module state."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("engine is not initialised; call init_engine() first")
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a session inside a transaction, committing on success."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        yield session


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
