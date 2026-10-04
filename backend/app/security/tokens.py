"""Session tokens, password generation and keyed hashing (PROJECT-SPEC §39.2, §42.1).

* Session tokens carry at least 256 bits of entropy and are stored only as a
  SHA-256 hash. The raw token lives only in the client cookie.
* Idempotency/abuse fingerprints use HMAC-SHA-256 with a server-side key, never
  a bare hash, because the input space is too small to resist offline guessing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string

# 48 random bytes -> 64 base64url chars -> 384 bits, comfortably above the
# 256-bit minimum.
SESSION_TOKEN_BYTES = 48

# Password alphabet without visually ambiguous characters.
_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
_PASSWORD_LENGTH = 20


def generate_session_token() -> str:
    """Return a new high-entropy session token (raw, client-facing)."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """Return the SHA-256 hex digest stored in ``admin_sessions.token_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_password() -> str:
    """Return a cryptographically random initial admin password."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))


def hmac_sha256_hex(key: str, message: str) -> str:
    """Return an HMAC-SHA-256 hex digest of ``message`` under ``key``."""
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
