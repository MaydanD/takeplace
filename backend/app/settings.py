"""Application configuration.

All configuration comes from environment variables (or a local ``.env`` in
development). Nothing here may be hardcoded: database URLs, credentials, CORS
origins and secrets are all injected per environment.

Production validation is fail-fast: a production deployment with placeholder
secrets, wildcard origins, or a debug log level refuses to start.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder shapes that must never survive into production. These target the
# literal defaults from .env.example and common copy-paste placeholders, not
# legitimate high-entropy values that merely contain such words.
_INSECURE_SECRET_MARKERS = (
    "change-me",
    "changeme",
    "change_me",
    "placeholder",
    "your-key",
    "your-key-here",
    "xxxxxxxx",
)


class Environment(StrEnum):
    """Deployment environment."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production(self) -> bool:
        return self is Environment.PRODUCTION


class Settings(BaseSettings):
    """Resolved application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TAKEPLACE_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- deployment ---------------------------------------------------------
    env: Environment = Environment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- API ----------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # --- database -----------------------------------------------------------
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "takeplace"
    # The application connects as the DML-only role (PROJECT-SPEC §46).
    db_app_user: str = "takeplace_app"
    # Development-only placeholder. Production validation rejects it (§39).
    db_app_password: str = "change-me-app"  # noqa: S105
    # The migrator role owns schema objects and is used only by Alembic.
    db_migrator_user: str = "takeplace_migrator"
    # Development-only placeholder. Production validation rejects it (§39).
    db_migrator_password: str = "change-me-migrator"  # noqa: S105

    db_pool_size: Annotated[int, Field(ge=1, le=50)] = 5
    db_max_overflow: Annotated[int, Field(ge=0, le=50)] = 5

    # Timeouts in milliseconds (PROJECT-SPEC §32.6).
    db_statement_timeout_ms: Annotated[int, Field(ge=100, le=600_000)] = 10_000
    db_lock_timeout_ms: Annotated[int, Field(ge=100, le=600_000)] = 3_000
    db_idle_in_transaction_timeout_ms: Annotated[int, Field(ge=1_000, le=600_000)] = 15_000
    db_migration_statement_timeout_ms: Annotated[int, Field(ge=1_000, le=3_600_000)] = 300_000

    # Explicit override used by tests / CI; when unset the DSN is composed.
    database_url: str | None = None
    test_database_url: str | None = None

    # --- CORS ---------------------------------------------------------------
    # Comma-separated list of exact origins. Never "*".
    cors_origins: str = ""

    # --- secrets ------------------------------------------------------------
    idempotency_hmac_key: str = "change-me-idempotency-hmac-key"
    abuse_hmac_key: str = "change-me-abuse-hmac-key"
    # Versioned key ring: "<version>:<base64url 32 bytes>", comma-separated.
    vk_encryption_keys: str = "1:change-me-32-byte-base64url-key"

    # --- derived ------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.env.is_production

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def _compose_url(self, user: str, password: str) -> str:
        from sqlalchemy import URL

        return URL.create(
            drivername="postgresql+asyncpg",
            username=user,
            password=password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)

    @property
    def app_database_url(self) -> str:
        """DSN for the request-serving application (DML-only role)."""
        if self.database_url:
            return self.database_url
        return self._compose_url(self.db_app_user, self.db_app_password)

    @property
    def migrator_database_url(self) -> str:
        """DSN for Alembic (DDL-capable migrator role)."""
        if self.database_url:
            return self.database_url
        return self._compose_url(self.db_migrator_user, self.db_migrator_password)

    # --- validation ---------------------------------------------------------
    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origins(cls, value: str) -> str:
        if "*" in value:
            raise ValueError("TAKEPLACE_CORS_ORIGINS must list exact origins; '*' is never allowed")
        return value

    @field_validator("cors_origins")
    @classmethod
    def _validate_origins(cls, value: str) -> str:
        for origin in (o.strip() for o in value.split(",")):
            if not origin:
                continue
            if not (origin.startswith("http://") or origin.startswith("https://")):
                raise ValueError(f"CORS origin must include scheme: {origin!r}")
        return value

    @model_validator(mode="after")
    def _validate_environment(self) -> Settings:
        if self.is_production:
            self._validate_production()
        return self

    def _validate_production(self) -> None:
        """Fail fast on insecure production configuration."""
        problems: list[str] = []

        if self.log_level == "DEBUG":
            problems.append("TAKEPLACE_LOG_LEVEL=DEBUG is not allowed in production")

        if not self.cors_origin_list:
            problems.append("TAKEPLACE_CORS_ORIGINS must list at least one exact origin")
        for origin in self.cors_origin_list:
            if not origin.startswith("https://"):
                problems.append(f"production CORS origin must use https://: {origin!r}")

        for name, value in (
            ("TAKEPLACE_IDEMPOTENCY_HMAC_KEY", self.idempotency_hmac_key),
            ("TAKEPLACE_ABUSE_HMAC_KEY", self.abuse_hmac_key),
            ("TAKEPLACE_DB_APP_PASSWORD", self.db_app_password),
            ("TAKEPLACE_DB_MIGRATOR_PASSWORD", self.db_migrator_password),
        ):
            if self._looks_insecure(value):
                problems.append(f"{name} looks like a placeholder or is too short")

        for version, key in self.vk_key_ring.items():
            try:
                decoded = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
            except (binascii.Error, ValueError):
                problems.append(f"TAKEPLACE_VK_ENCRYPTION_KEYS[{version}] is not valid base64url")
                continue
            if len(decoded) != 32:
                problems.append(f"TAKEPLACE_VK_ENCRYPTION_KEYS[{version}] must decode to 32 bytes")

        if problems:
            joined = "\n  - ".join(problems)
            raise ValueError(f"invalid production configuration:\n  - {joined}")

    @staticmethod
    def _looks_insecure(value: str) -> bool:
        lowered = value.strip().lower()
        if len(lowered) < 16:
            return True
        return any(marker in lowered for marker in _INSECURE_SECRET_MARKERS)

    @property
    def vk_key_ring(self) -> dict[int, str]:
        """Parsed VK encryption key ring, keyed by key version."""
        ring: dict[int, str] = {}
        for entry in self.vk_encryption_keys.split(","):
            entry = entry.strip()
            if not entry:
                continue
            version_str, _, key = entry.partition(":")
            if not key:
                raise ValueError(
                    "TAKEPLACE_VK_ENCRYPTION_KEYS entries must look like '<version>:<key>'"
                )
            ring[int(version_str)] = key
        if not ring:
            raise ValueError("TAKEPLACE_VK_ENCRYPTION_KEYS must contain at least one key")
        return ring


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def trusted_proxy_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Networks whose forwarded-client headers may be trusted.

    Uvicorn only trusts ``X-Forwarded-*`` from these networks. The reverse proxy
    (Caddy / the internal Docker network) is the only legitimate source
    (PROJECT-SPEC §39.5).
    """
    return [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
        # Docker's default bridge / compose network ranges.
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("10.0.0.0/8"),
    ]
