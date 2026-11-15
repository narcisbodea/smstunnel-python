from __future__ import annotations

import asyncio
import json

import pytest

from smstunnel import ApiError, AsyncClient, Client, PermissionDeniedError, TransportError

from .support import DEVICES, KEY, SENT

pytest.importorskip("httpx")

import httpx

from smstunnel.transport import AsyncHttpxTransport, HttpxTransport


def test_sends_through_an_httpx_client() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SENT)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = Client(KEY, transport=HttpxTransport(http))
        result = client.send_sms("+14155550123", "Hi from httpx")
        client.close()
        assert not http.is_closed, "a client passed in stays open"

    assert result.message_id == "6ae7957285431f6150ce52fc"
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://smstunnel.io/api/v1/sms/send"
    assert seen[0].headers["x-api-key"] == KEY
    assert json.loads(seen[0].content) == {"to": "+14155550123", "message": "Hi from httpx"}


def test_the_idempotency_key_goes_through_httpx() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SENT)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = Client(KEY, transport=HttpxTransport(http))
        client.send_verification_code("+14155550123", "Your sign-in code is 482913", idempotency_key="sign-in-77")

    assert str(seen[0].url) == "https://smstunnel.io/api/v1/sms/send-2fa"
    assert seen[0].headers["idempotency-key"] == "sign-in-77"


def test_does_not_follow_redirects_and_maps_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/devices":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example/"})
        if request.url.path == "/api/v1/account/usage":
            return httpx.Response(403, json={"message": "Upgrade needed", "code": "PLAN_REQUIRED"})
        raise httpx.ConnectError("connection refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        transport = HttpxTransport(http)
        client = Client(KEY, transport=transport)

        with pytest.raises(ApiError, match=r"redirect to https://elsewhere\.example/"):
            client.get_devices()
        with pytest.raises(PermissionDeniedError) as caught:
            client.get_usage()
        assert caught.value.error_code == "PLAN_REQUIRED"
        with pytest.raises(TransportError, match="connection refused"):
            client.get_message_status("m1")


def test_an_httpx_timeout_is_a_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = Client(KEY, timeout=2, transport=HttpxTransport(http))
        with pytest.raises(TransportError, match=r"no answer within 2 s"):
            client.get_devices()


def test_the_async_client_through_httpx() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DEVICES)

    async def scenario() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with AsyncClient(KEY, transport=AsyncHttpxTransport(http)) as client:
                phones = await client.get_devices()
            assert not http.is_closed
            return phones.total

    assert asyncio.run(scenario()) == 2


def test_a_transport_it_made_is_closed_with_the_client() -> None:
    transport = HttpxTransport()
    transport.close()
    assert transport._client.is_closed
