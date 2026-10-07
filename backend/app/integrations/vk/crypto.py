"""VK access-token encryption at rest (PROJECT-SPEC §38.5).

The token is encrypted with AES-256-GCM under a versioned key ring that lives
*outside* the database (``TAKEPLACE_VK_ENCRYPTION_KEYS``). The stored ciphertext is
bound to its ``encryption_key_version`` so a key can be rotated while old rows are
still decryptable.

Security properties enforced here:

* the key ring is parsed once and validated (each entry must be 32 bytes);
* the token is never logged, never returned to the frontend and never embedded in
  an exception message — errors carry only the key version, never key material;
* the nonce is random per encryption and stored alongside the ciphertext;
* the GCM tag authenticates the ciphertext, so a tampered row fails to decrypt.

Ciphertext layout (base64url, no padding)::

    version(1 byte) || nonce(12 bytes) || ciphertext+tag

The leading version byte lets a future reader detect the format without a schema
change; ``encryption_key_version`` on the row selects the AES key.
"""

from __future__ import annotations

import base64
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12
_FORMAT_VERSION = 1


class VKEncryptionError(RuntimeError):
    """Raised when a token cannot be encrypted or decrypted (no secret in the message)."""


def _decode_key(key: str) -> bytes:
    """Decode a base64url key, requiring exactly 32 bytes (AES-256)."""
    try:
        raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise VKEncryptionError("VK encryption key is not valid base64url") from exc
    if len(raw) != 32:
        raise VKEncryptionError("VK encryption key must decode to 32 bytes")
    return raw


class VKTokenCipher:
    """Encrypt/decrypt VK access tokens under a versioned key ring."""

    def __init__(self, key_ring: dict[int, str]) -> None:
        if not key_ring:
            raise VKEncryptionError("VK encryption key ring must contain at least one key")
        self._keys = {version: _decode_key(key) for version, key in key_ring.items()}
        self._primary_version = max(self._keys)

    @property
    def primary_version(self) -> int:
        """The version used for new encryptions (highest configured version)."""
        return self._primary_version

    def encrypt(self, token: str) -> tuple[str, int]:
        """Encrypt ``token``; return ``(ciphertext_b64, key_version)``."""
        if not token:
            raise VKEncryptionError("cannot encrypt an empty VK token")
        version = self._primary_version
        nonce = os.urandom(_NONCE_BYTES)
        aesgcm = AESGCM(self._keys[version])
        sealed = aesgcm.encrypt(nonce, token.encode("utf-8"), None)
        blob = struct.pack(">B", _FORMAT_VERSION) + nonce + sealed
        return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("="), version

    def decrypt(self, ciphertext: str, key_version: int) -> str:
        """Decrypt a stored token; raise ``VKEncryptionError`` on any failure."""
        key = self._keys.get(key_version)
        if key is None:
            # The message names the missing version but never the key material.
            raise VKEncryptionError(f"no VK encryption key for version {key_version}")
        try:
            blob = base64.urlsafe_b64decode(ciphertext + "=" * (-len(ciphertext) % 4))
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise VKEncryptionError("stored VK token is not valid base64url") from exc
        if len(blob) < 1 + _NONCE_BYTES + 16:
            raise VKEncryptionError("stored VK token is truncated")
        fmt = blob[0]
        if fmt != _FORMAT_VERSION:
            raise VKEncryptionError(f"unsupported VK token format version {fmt}")
        nonce = blob[1 : 1 + _NONCE_BYTES]
        sealed = blob[1 + _NONCE_BYTES :]
        try:
            plaintext = AESGCM(key).decrypt(nonce, sealed, None)
        except InvalidTag as exc:
            raise VKEncryptionError("stored VK token failed authentication") from exc
        return plaintext.decode("utf-8")
