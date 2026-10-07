"""Configuration tests: environment parsing and fail-fast production rules."""

from __future__ import annotations

import pytest
from app.settings import Environment, Settings
from pydantic import ValidationError

_GOOD_SECRET = "a-sufficiently-long-secret-value-0123456789"
_GOOD_VK_KEY = "1:" + "A" * 43  # base64url, decodes to 32 bytes


def _production_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "env": "production",
        "log_level": "INFO",
        "cors_origins": "https://takeplace.example",
        "idempotency_hmac_key": _GOOD_SECRET,
        "abuse_hmac_key": _GOOD_SECRET,
        "db_app_password": _GOOD_SECRET,
        "db_migrator_password": _GOOD_SECRET,
        "vk_encryption_keys": _GOOD_VK_KEY,
    }
    base.update(overrides)
    return base


def test_development_defaults_are_valid() -> None:
    settings = Settings(env="development")
    assert settings.env is Environment.DEVELOPMENT
    assert settings.is_production is False


def test_wildcard_cors_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(env="development", cors_origins="*")


def test_cors_origin_requires_scheme() -> None:
    with pytest.raises(ValidationError):
        Settings(env="development", cors_origins="localhost:5173")


def test_cors_origins_are_split() -> None:
    settings = Settings(
        env="development",
        cors_origins="http://localhost:5173, http://localhost:4173",
    )
    assert settings.cors_origin_list == [
        "http://localhost:5173",
        "http://localhost:4173",
    ]


def test_production_configuration_accepts_secure_values() -> None:
    settings = Settings(**_production_kwargs())  # type: ignore[arg-type]
    assert settings.is_production is True


def test_production_rejects_debug_log_level() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        Settings(**_production_kwargs(log_level="DEBUG"))  # type: ignore[arg-type]


def test_production_rejects_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="IDEMPOTENCY_HMAC_KEY"):
        Settings(**_production_kwargs(idempotency_hmac_key="change-me"))  # type: ignore[arg-type]


def test_production_rejects_http_cors_origin() -> None:
    with pytest.raises(ValidationError, match="https"):
        Settings(**_production_kwargs(cors_origins="http://takeplace.example"))  # type: ignore[arg-type]


def test_production_requires_cors_origins() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(**_production_kwargs(cors_origins=""))  # type: ignore[arg-type]


def test_production_rejects_wildcard_forwarded_allow_ips() -> None:
    with pytest.raises(ValidationError, match="FORWARDED_ALLOW_IPS"):
        Settings(**_production_kwargs(forwarded_allow_ips="*"))  # type: ignore[arg-type]


def test_production_rejects_short_vk_key() -> None:
    with pytest.raises(ValidationError, match="VK_ENCRYPTION_KEYS"):
        Settings(**_production_kwargs(vk_encryption_keys="1:c2hvcnQ"))  # type: ignore[arg-type]


def test_app_database_url_uses_app_role() -> None:
    settings = Settings(env="development", db_app_user="app", db_app_password="pw")
    assert "app:" in settings.app_database_url
    assert settings.app_database_url.startswith("postgresql+asyncpg://")


def test_migrator_database_url_uses_migrator_role() -> None:
    settings = Settings(env="development", db_migrator_user="migrator", db_migrator_password="pw")
    assert "migrator:" in settings.migrator_database_url


def test_database_url_override_wins() -> None:
    settings = Settings(env="development", database_url="postgresql+asyncpg://x:y@h/db")
    assert settings.app_database_url == "postgresql+asyncpg://x:y@h/db"
    assert settings.migrator_database_url == "postgresql+asyncpg://x:y@h/db"


def test_cookie_secure_defaults_to_production_only() -> None:
    assert Settings(env="development").cookie_secure is False
    assert Settings(env="staging").cookie_secure is False
    assert Settings(**_production_kwargs()).cookie_secure is True  # type: ignore[arg-type]


def test_cookie_secure_can_be_forced_off_outside_production() -> None:
    settings = Settings(env="development", session_cookie_secure=False)
    assert settings.cookie_secure is False


def test_production_rejects_insecure_cookie_override() -> None:
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE"):
        Settings(**_production_kwargs(session_cookie_secure=False))  # type: ignore[arg-type]


def test_vk_key_ring_parses_versions() -> None:
    settings = Settings(
        env="development", vk_encryption_keys=f"1:{_GOOD_VK_KEY[2:]},2:{_GOOD_VK_KEY[2:]}"
    )
    assert set(settings.vk_key_ring) == {1, 2}


# --- VK worker configuration (PROJECT-SPEC §38) -----------------------------


def test_vk_worker_defaults_are_sane() -> None:
    settings = Settings(env="development")
    assert settings.vk_worker_enabled is True
    assert settings.vk_notification_max_age_seconds == 6 * 3600
    assert settings.vk_notification_late_grace_seconds == 30 * 60
    assert settings.vk_worker_max_attempts >= 1
    assert settings.vk_worker_retry_base_seconds <= settings.vk_worker_retry_max_seconds
    assert settings.vk_api_version


def test_vk_late_grace_default_is_thirty_minutes() -> None:
    # The spec fixes the deploy default of the late-grace window (§38.2).
    assert Settings(env="development").vk_notification_late_grace_seconds == 1800


def test_vk_late_grace_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        Settings(env="development", vk_notification_late_grace_seconds=-1)


def test_vk_worker_lease_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(env="development", vk_worker_lease_seconds=0)


def test_vk_settings_are_not_production_placeholders() -> None:
    # VK worker settings are operational config, not secrets, and must survive
    # production validation unchanged.
    settings = Settings(**_production_kwargs())  # type: ignore[arg-type]
    assert settings.vk_worker_enabled is True


# --- Stage 13: retention, proxy trust and PostgreSQL-only validation --------


def test_production_rejects_empty_forwarded_allow_ips() -> None:
    # An empty allow-list means the real client IP behind Caddy is never resolved.
    with pytest.raises(ValidationError, match="FORWARDED_ALLOW_IPS"):
        Settings(**_production_kwargs(forwarded_allow_ips=""))  # type: ignore[arg-type]


def test_non_postgres_database_url_is_rejected() -> None:
    # v1 requires PostgreSQL; SQLite must fail fast rather than silently degrade
    # concurrency/exclusion guarantees (§46, §62).
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings(env="development", database_url="sqlite+aiosqlite:///tmp/x.db")


def test_retention_defaults_and_bounds() -> None:
    settings = Settings(env="development")
    assert settings.booking_pii_retention_days >= 1
    assert settings.outbox_retention_days >= 1
    assert settings.maintenance_interval_seconds >= 60
    assert settings.maintenance_batch_size >= 1
    with pytest.raises(ValidationError):
        Settings(env="development", booking_pii_retention_days=0)


def test_retention_settings_survive_production_validation() -> None:
    settings = Settings(**_production_kwargs())  # type: ignore[arg-type]
    assert settings.booking_pii_retention_days >= 1


def test_maintenance_stuck_lease_must_not_be_shorter_than_worker_lease() -> None:
    with pytest.raises(ValidationError, match="maintenance_stuck_lease_seconds"):
        Settings(
            env="development",
            maintenance_stuck_lease_seconds=60,
            vk_worker_lease_seconds=120,
        )
