"""Unit tests for VK token encryption at rest (PROJECT-SPEC §38.5)."""

from __future__ import annotations

import base64
import os

import pytest
from app.integrations.vk.crypto import VKEncryptionError, VKTokenCipher


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def test_roundtrip_encrypt_decrypt() -> None:
    cipher = VKTokenCipher({1: _key()})
    token = "vk1.a.secret-access-token"
    ciphertext, version = cipher.encrypt(token)
    assert version == 1
    assert ciphertext != token
    assert cipher.decrypt(ciphertext, version) == token


def test_ciphertext_is_not_plaintext_and_uses_primary_version() -> None:
    cipher = VKTokenCipher({1: _key(), 2: _key()})
    ciphertext, version = cipher.encrypt("token")
    assert version == 2  # highest configured version is primary
    assert "token" not in ciphertext


def test_old_version_ciphertext_still_decrypts_after_rotation() -> None:
    """A newer key ring that still holds the old key can read old rows (§38.5)."""
    key1 = _key()
    old = VKTokenCipher({1: key1})
    ciphertext, version = old.encrypt("rotate-me")
    rotated = VKTokenCipher({1: key1, 2: _key()})
    # New writes use version 2, but the old row keeps decrypting under version 1.
    assert rotated.decrypt(ciphertext, version) == "rotate-me"
    assert rotated.primary_version == 2


def test_missing_key_version_raises_without_leaking_key() -> None:
    cipher = VKTokenCipher({1: _key()})
    ciphertext, _ = cipher.encrypt("token")
    with pytest.raises(VKEncryptionError) as info:
        cipher.decrypt(ciphertext, 9)
    assert "9" in str(info.value)


def test_tampered_ciphertext_fails_authentication() -> None:
    cipher = VKTokenCipher({1: _key()})
    ciphertext, version = cipher.encrypt("token")
    raw = bytearray(base64.urlsafe_b64decode(ciphertext + "=" * (-len(ciphertext) % 4)))
    raw[-1] ^= 0xFF
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    with pytest.raises(VKEncryptionError):
        cipher.decrypt(tampered, version)


def test_garbage_ciphertext_raises() -> None:
    cipher = VKTokenCipher({1: _key()})
    with pytest.raises(VKEncryptionError):
        cipher.decrypt("not-base64!!", 1)


def test_invalid_key_material_is_rejected() -> None:
    with pytest.raises(VKEncryptionError):
        VKTokenCipher({1: "short"})


def test_empty_key_ring_is_rejected() -> None:
    with pytest.raises(VKEncryptionError):
        VKTokenCipher({})


def test_encrypting_empty_token_is_rejected() -> None:
    cipher = VKTokenCipher({1: _key()})
    with pytest.raises(VKEncryptionError):
        cipher.encrypt("")


def test_error_messages_never_contain_the_token() -> None:
    cipher = VKTokenCipher({1: _key()})
    secret = "vk1.a.very-secret-token-value"
    ciphertext, version = cipher.encrypt(secret)
    raw = bytearray(base64.urlsafe_b64decode(ciphertext + "=" * (-len(ciphertext) % 4)))
    raw[-1] ^= 0xFF
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    with pytest.raises(VKEncryptionError) as info:
        cipher.decrypt(tampered, version)
    assert secret not in str(info.value)
