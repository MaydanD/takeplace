"""Argon2id password hashing (PROJECT-SPEC §39.1)."""

from __future__ import annotations

import pytest
from app.security.passwords import PasswordHasher

_PASSWORD = "correct horse battery staple"


@pytest.fixture
def hasher() -> PasswordHasher:
    return PasswordHasher(max_concurrency=2)


async def test_hash_produces_argon2id(hasher: PasswordHasher) -> None:
    digest = await hasher.hash(_PASSWORD)
    assert digest.startswith("$argon2id$")
    assert _PASSWORD not in digest


async def test_hash_is_salted(hasher: PasswordHasher) -> None:
    assert await hasher.hash(_PASSWORD) != await hasher.hash(_PASSWORD)


async def test_verify_accepts_correct_password(hasher: PasswordHasher) -> None:
    digest = await hasher.hash(_PASSWORD)
    assert await hasher.verify(digest, _PASSWORD) is True


async def test_verify_rejects_wrong_password(hasher: PasswordHasher) -> None:
    digest = await hasher.hash(_PASSWORD)
    assert await hasher.verify(digest, "wrong") is False


async def test_verify_rejects_malformed_hash(hasher: PasswordHasher) -> None:
    assert await hasher.verify("not-a-hash", _PASSWORD) is False


async def test_verify_dummy_never_raises(hasher: PasswordHasher) -> None:
    assert await hasher.verify_dummy("anything") is None


async def test_current_hash_does_not_need_rehash(hasher: PasswordHasher) -> None:
    digest = await hasher.hash(_PASSWORD)
    assert hasher.needs_rehash(digest) is False


def test_malformed_hash_needs_rehash(hasher: PasswordHasher) -> None:
    assert hasher.needs_rehash("nonsense") is True
