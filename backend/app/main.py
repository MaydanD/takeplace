"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, health, public
from app.api.errors import ApiError, api_error_handler
from app.db.session import dispose_engine, get_session_factory, init_engine
from app.logging_config import configure_logging, get_logger
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.realtime import RealtimeListener, init_hub, reset_hub
from app.security.limits import init_limiters, reset_limiters
from app.security.passwords import init_password_hasher, reset_password_hasher
from app.services.public_rate_limit import init_public_rate_limiters, reset_public_rate_limiters
from app.services.timezone_capability import init_monitor, reset_monitor
from app.settings import Settings, get_settings

logger = get_logger("takeplace.app")

API_DESCRIPTION = """
Takeplace HTTP API.

* Public booking API: `/api/public/v1`
* Admin API: `/api/admin/v1`
* Health: `/health/live`, `/health/ready`, `/health/ops`

This OpenAPI document is the single source of API transport types for the
frontend. Types are generated with `openapi-typescript` and never edited by hand.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        # Stage 2 process-wide services. Each is (re)initialised here so a test
        # or reload builds a coherent generation with no stale state.
        reset_password_hasher()
        init_password_hasher(settings.argon2_max_concurrency)
        reset_limiters()
        init_limiters(settings)
        reset_public_rate_limiters()
        init_public_rate_limiters(settings)
        reset_monitor()
        monitor = init_monitor(get_session_factory(), horizon_days=settings.timezone_horizon_days)
        monitor_task = asyncio.create_task(monitor.run_forever())
        # Realtime: one process-wide hub plus the dedicated LISTEN connection.
        reset_hub()
        hub = init_hub(queue_maxsize=settings.realtime_queue_maxsize)
        listener = RealtimeListener(
            settings.app_database_url,
            hub,
            enabled=settings.realtime_listener_enabled,
        )
        # Exposed for readiness/diagnostics and tests; not part of the API surface.
        _app.state.realtime_listener = listener
        await listener.start()
        logger.info(
            "application_startup",
            environment=settings.env.value,
            log_level=settings.log_level,
        )
        try:
            yield
        finally:
            await listener.stop()
            reset_hub()
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task
            reset_monitor()
            reset_public_rate_limiters()
            reset_limiters()
            reset_password_hasher()
            await dispose_engine()
            logger.info("application_shutdown")

    app = FastAPI(
        title="Takeplace API",
        version="0.1.0",
        description=API_DESCRIPTION,
        lifespan=lifespan,
        # Hide interactive docs outside non-production environments.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url="/openapi.json",
    )

    # Middleware is applied outside-in: security headers wrap the context
    # middleware so that even error responses carry the baseline headers.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)

    # CORS is deliberately narrow: only exact configured origins (PROJECT-SPEC
    # §39.3). Credentials are allowed because admin cookies are SameSite=Strict
    # and only reachable from the same origin in production.
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Idempotency-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    app.include_router(health.router)
    app.include_router(public.router)
    app.include_router(admin.router)
    # Expected, user-actionable failures map to stable machine-readable codes
    # (PROJECT-SPEC §36) instead of generic 500 responses.
    app.add_exception_handler(ApiError, api_error_handler)
    return app


app = create_app()


def main() -> None:
    """Run the development server."""
    import uvicorn

    settings = get_settings()
    structlog.configure()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=False,  # structured middleware omits PII-bearing query strings
        # Trust X-Forwarded-* only from the configured proxy addresses
        # (PROJECT-SPEC §39.5).
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
