"""Argon2id password hashing (PROJECT-SPEC §39.1).

Argon2id is CPU-bound and must never run directly in the FastAPI event loop.
Hashing and verification run on a thread pool bounded by a semaphore, so a burst
of login attempts cannot saturate every worker thread.
"""

from __future__ import annotations

import anyio
import anyio.to_thread
from argon2 import PasswordHasher as _Argon2PasswordHasher
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# A fixed, valid hash used to equalize timing when the login does not exist, so
# "wrong login" and "wrong password" are indistinguishable by latency (§39.5).
_DUMMY_PASSWORD = "takeplace-dummy-password-for-constant-time-verify"  # noqa: S105


class PasswordHasher:
    """Argon2id hasher with bounded concurrency."""

    def __init__(self, max_concurrency: int) -> None:
        self._hasher = _Argon2PasswordHasher(type=Type.ID)
        self._semaphore = anyio.Semaphore(max_concurrency)
        self._dummy_hash = self._hasher.hash(_DUMMY_PASSWORD)

    async def hash(self, password: str) -> str:
        async with self._semaphore:
            return await anyio.to_thread.run_sync(self._hasher.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        async with self._semaphore:
            return await anyio.to_thread.run_sync(self._verify, password_hash, password)

    async def verify_dummy(self, password: str) -> None:
        """Perform a throwaway verify to keep failure timing constant."""
        async with self._semaphore:
            await anyio.to_thread.run_sync(self._verify, self._dummy_hash, password)

    @staticmethod
    def _verify(password_hash: str, password: str) -> bool:
        try:
            _Argon2PasswordHasher().verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        return True

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True


_hasher: PasswordHasher | None = None


def init_password_hasher(max_concurrency: int) -> PasswordHasher:
    global _hasher
    if _hasher is None:
        _hasher = PasswordHasher(max_concurrency)
    return _hasher


def get_password_hasher() -> PasswordHasher:
    if _hasher is None:
        raise RuntimeError("password hasher is not initialised")
    return _hasher


def reset_password_hasher() -> None:
    global _hasher
    _hasher = None
