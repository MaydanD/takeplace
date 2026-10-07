"""Unit tests for the VK API client (PROJECT-SPEC §38.3, §38.4).

These use a controlled ``httpx.MockTransport`` so the suite never performs a real
network request, while still exercising the real request/response handling:
timeouts, HTTP status classification, VK ``error`` payloads and the ``random_id``
derivation.
"""

from __future__ import annotations

import httpx
import pytest
from app.integrations.vk.client import (
    VKClient,
    VKDeliveryError,
    VKErrorClass,
    random_id_for,
)

# Async tests run under ``asyncio_mode = auto`` (pyproject), so no module-level
# asyncio mark is needed — and marking sync tests would emit warnings.


def _client(handler: httpx.MockTransport) -> VKClient:
    return VKClient(
        api_version="5.199",
        timeout_seconds=1.0,
        api_base="https://api.vk.test/method",
        transport=handler,
    )


async def test_successful_send_parses_message_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"response": 4242})

    client = _client(httpx.MockTransport(handler))
    result = await client.send_message(
        access_token="secret-token",
        peer_id=2000000001,
        message="hello",
        provider_dedup_id="dedup-1",
    )
    assert result.message_id == 4242
    assert "/messages.send" in str(captured["url"])
    assert "random_id=" in str(captured["body"])


async def test_vk_error_payload_is_not_treated_as_delivery() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"error_code": 100, "error_msg": "bad param"}})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError) as info:
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")
    assert info.value.error_class is VKErrorClass.PERMANENT
    assert info.value.vk_error_code == 100


async def test_retryable_vk_error_code() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"error_code": 6}})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError) as info:
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")
    assert info.value.error_class is VKErrorClass.RETRYABLE
    assert info.value.retryable is True


async def test_http_500_is_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError) as info:
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")
    assert info.value.error_class is VKErrorClass.RETRYABLE


async def test_http_429_is_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError) as info:
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")
    assert info.value.error_class is VKErrorClass.RETRYABLE


async def test_http_403_is_permanent() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError) as info:
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")
    assert info.value.error_class is VKErrorClass.PERMANENT


async def test_network_error_is_retryable_and_hides_token() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError) as info:
        await client.send_message(
            access_token="super-secret-token",
            peer_id=1,
            message="m",
            provider_dedup_id="d",
        )
    assert info.value.error_class is VKErrorClass.RETRYABLE
    assert "super-secret-token" not in str(info.value)


async def test_timeout_is_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError) as info:
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")
    assert info.value.error_class is VKErrorClass.RETRYABLE


async def test_non_json_response_is_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy error</html>")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError) as info:
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")
    assert info.value.error_class is VKErrorClass.RETRYABLE


async def test_response_without_message_id_is_not_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": {"unexpected": True}})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(VKDeliveryError):
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")


def test_random_id_is_stable_and_in_range() -> None:
    first = random_id_for("takeplace-abc")
    second = random_id_for("takeplace-abc")
    assert first == second  # stable across retries -> VK deduplicates (§38.4)
    assert 0 <= first < 2**31
    assert first != random_id_for("takeplace-xyz")


def test_vk_error_codes_classified_as_expected() -> None:
    from app.integrations.vk.client import _error_from_vk_code

    assert _error_from_vk_code(5).error_class is VKErrorClass.PERMANENT  # bad token
    assert _error_from_vk_code(113).error_class is VKErrorClass.PERMANENT  # bad peer
    assert _error_from_vk_code(9).error_class is VKErrorClass.RETRYABLE  # flood control
    assert _error_from_vk_code(10).error_class is VKErrorClass.RETRYABLE  # internal
    assert _error_from_vk_code(None).error_class is VKErrorClass.RETRYABLE


@pytest.mark.parametrize("bad_value", [True, False, 0, -1, "777", None])
async def test_success_requires_positive_integer_message_id(bad_value) -> None:
    client = _client(
        httpx.MockTransport(lambda _: httpx.Response(200, json={"response": bad_value}))
    )
    with pytest.raises(VKDeliveryError):
        await client.send_message(access_token="t", peer_id=1, message="m", provider_dedup_id="d")
