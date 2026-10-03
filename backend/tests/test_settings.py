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


def test_vk_key_ring_parses_versions() -> None:
    settings = Settings(
        env="development", vk_encryption_keys=f"1:{_GOOD_VK_KEY[2:]},2:{_GOOD_VK_KEY[2:]}"
    )
    assert set(settings.vk_key_ring) == {1, 2}
