"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, health, public
from app.db.session import dispose_engine, init_engine
from app.logging_config import configure_logging, get_logger
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
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
        logger.info(
            "application_startup",
            environment=settings.env.value,
            log_level=settings.log_level,
        )
        try:
            yield
        finally:
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
        # Trust X-Forwarded-* only from the configured proxy addresses
        # (PROJECT-SPEC §39.5).
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
