"""Session token, password generation and keyed hashing (PROJECT-SPEC §39.2, §42.1)."""

from __future__ import annotations

import re

from app.security.tokens import (
    generate_password,
    generate_session_token,
    hash_session_token,
    hmac_sha256_hex,
)


def test_session_token_is_high_entropy_and_url_safe() -> None:
    token = generate_session_token()
    # 48 random bytes -> 64 base64url characters.
    assert len(token) >= 43
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", token)
    # At least 256 bits of entropy.
    assert len(token) * 6 >= 256


def test_session_tokens_are_unique() -> None:
    assert len({generate_session_token() for _ in range(500)}) == 500


def test_hash_session_token_is_sha256_hex_and_deterministic() -> None:
    token = generate_session_token()
    digest = hash_session_token(token)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert hash_session_token(token) == digest
    assert digest != token


def test_generated_password_has_expected_shape() -> None:
    password = generate_password()
    assert len(password) == 20
    assert password.isascii()


def test_generated_passwords_are_unique() -> None:
    assert len({generate_password() for _ in range(200)}) == 200


def test_hmac_is_keyed_and_deterministic() -> None:
    message = "203.0.113.7"
    a = hmac_sha256_hex("key-a", message)
    b = hmac_sha256_hex("key-a", message)
    c = hmac_sha256_hex("key-b", message)
    assert a == b
    assert a != c
    assert re.fullmatch(r"[0-9a-f]{64}", a)
