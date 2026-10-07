"""VK API client (PROJECT-SPEC §38.3, §38.4).

An isolated async client for the single operation Takeplace performs: posting a
message to a staff conversation. It is deliberately small and injectable so the
whole suite can run against a controlled fake transport without ever touching the
real VK.

Contract:

* explicit timeouts (connect/read/write/pool) on every request;
* network failures, HTTP failures and VK ``error`` payloads are all surfaced as a
  classified ``VKDeliveryError`` — a VK API error is never mistaken for success;
* the access token is passed in the request body (VK's documented flow) and is
  never logged; exceptions carry only a machine-readable classification;
* ``random_id`` is derived from a stable ``provider_dedup_id`` so a resend after a
  crash is deduplicated by VK instead of double-posting (§38.4).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import StrEnum

import httpx

logger = logging.getLogger("takeplace.vk.client")

#: VK 5.199 schema declares an integer dedup key; use the conservative nonzero
#: positive int32 subset. https://github.com/VKCOM/vk-api-schema/blob/master/messages/methods.json
_RANDOM_ID_MODULUS = 2**31


class VKErrorClass(StrEnum):
    """Classification that drives the worker's retry/permanent decision (§38.3)."""

    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"


class VKDeliveryError(Exception):
    """A VK send failed. ``retryable`` decides retry vs terminal (§38.3).

    The message never contains the token, the request body or any PII — only the
    error class, the VK error code (if any) and a short, safe description.
    """

    def __init__(
        self,
        message: str,
        *,
        error_class: VKErrorClass,
        vk_error_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.vk_error_code = vk_error_code

    @property
    def retryable(self) -> bool:
        return self.error_class is VKErrorClass.RETRYABLE


@dataclass(frozen=True, slots=True)
class VKSendResult:
    """A successful send: VK echoed the message id it created."""

    message_id: int


def random_id_for(provider_dedup_id: str) -> int:
    """Derive a stable signed 32-bit ``random_id`` from the provider dedup id (§38.4).

    The outbox ``id`` is *not* used directly: the spec warns that a ``BIGINT`` id
    is not guaranteed to be a valid VK ``random_id`` until the API version is
    confirmed. Hashing the stable ``provider_dedup_id`` yields a value in range and
    keeps it identical across retries, so VK deduplicates a resend.
    """
    digest = hashlib.sha256(provider_dedup_id.encode("utf-8")).digest()
    return 1 + int.from_bytes(digest[:4], "big") % (_RANDOM_ID_MODULUS - 1)


# VK error codes that are transient and worth retrying (§38.3).
_RETRYABLE_VK_CODES = frozenset(
    {
        1,  # Unknown error
        6,  # Too many requests per second
        9,  # Flood control
        10,  # Internal server error
        29,  # Rate limit reached
    }
)
# Codes that will never succeed on retry: bad token, bad peer, forbidden op.
_PERMANENT_VK_CODES = frozenset(
    {
        5,  # User authorization failed (revoked/invalid token)
        15,  # Access denied
        100,  # One of the parameters is invalid
        113,  # Invalid user id / peer
        901,  # Can't send messages to this peer
        914,  # Message is too long
        940,  # Bad request
    }
)


class VKClient:
    """Minimal async VK API client for ``messages.send``."""

    def __init__(
        self,
        *,
        api_version: str,
        timeout_seconds: float = 10.0,
        api_base: str = "https://api.vk.com/method",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_version = api_version
        self._api_base = api_base.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        # A caller-supplied transport is how tests inject a fake without touching
        # the network; ``None`` uses the real default transport.
        self._transport = transport

    async def send_message(
        self,
        *,
        access_token: str,
        peer_id: int,
        message: str,
        provider_dedup_id: str,
    ) -> VKSendResult:
        """Send ``message`` to ``peer_id``; raise ``VKDeliveryError`` on failure."""
        url = f"{self._api_base}/messages.send"
        data = {
            "access_token": access_token,
            "v": self._api_version,
            "peer_id": peer_id,
            "message": message,
            "random_id": random_id_for(provider_dedup_id),
            "disable_mentions": 1,
            "dont_parse_links": 1,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(url, data=data)
        except httpx.TimeoutException as exc:
            # Network/timeout failures are retryable; never echo the token or body.
            raise VKDeliveryError(
                "VK request timed out", error_class=VKErrorClass.RETRYABLE
            ) from exc
        except httpx.HTTPError as exc:
            raise VKDeliveryError("VK network error", error_class=VKErrorClass.RETRYABLE) from exc

        if response.status_code >= 500:
            raise VKDeliveryError(
                f"VK HTTP {response.status_code}", error_class=VKErrorClass.RETRYABLE
            )
        if response.status_code == 429:
            raise VKDeliveryError("VK rate limited", error_class=VKErrorClass.RETRYABLE)
        if response.status_code >= 400:
            # 4xx other than 429 is not going to fix itself on retry.
            raise VKDeliveryError(
                f"VK HTTP {response.status_code}", error_class=VKErrorClass.PERMANENT
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise VKDeliveryError(
                "VK returned a non-JSON response", error_class=VKErrorClass.RETRYABLE
            ) from exc

        error = payload.get("error") if isinstance(payload, dict) else None
        if error is not None:
            code = error.get("error_code") if isinstance(error, dict) else None
            raise _error_from_vk_code(code)

        response_value = payload.get("response") if isinstance(payload, dict) else None
        message_id = (
            response_value
            if isinstance(response_value, int)
            and not isinstance(response_value, bool)
            and response_value > 0
            else None
        )
        if message_id is None:
            # A response without a message id cannot be treated as delivered.
            raise VKDeliveryError(
                "VK response did not contain a message id",
                error_class=VKErrorClass.RETRYABLE,
            )
        return VKSendResult(message_id=message_id)


def _error_from_vk_code(code: object) -> VKDeliveryError:
    """Map a VK ``error_code`` to a classified delivery error."""
    if not isinstance(code, int):
        # Unknown error shape: treat as retryable so a transient change is not
        # turned into a permanent loss, but never as success.
        return VKDeliveryError(
            "VK returned an unrecognised error", error_class=VKErrorClass.RETRYABLE
        )
    if code in _RETRYABLE_VK_CODES:
        return VKDeliveryError(
            f"VK error {code} (retryable)",
            error_class=VKErrorClass.RETRYABLE,
            vk_error_code=code,
        )
    if code in _PERMANENT_VK_CODES:
        return VKDeliveryError(
            f"VK error {code} (permanent)",
            error_class=VKErrorClass.PERMANENT,
            vk_error_code=code,
        )
    # Unlisted codes default to permanent so the worker does not loop forever on
    # an error that keeps failing (bounded by max attempts regardless).
    return VKDeliveryError(
        f"VK error {code} (unclassified, treated as permanent)",
        error_class=VKErrorClass.PERMANENT,
        vk_error_code=code,
    )
