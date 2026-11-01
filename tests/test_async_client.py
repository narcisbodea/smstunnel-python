from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from smstunnel import AsyncClient, AuthenticationError, RateLimitError
from smstunnel.transport import AsyncHttpxTransport, ThreadedAsyncTransport

from .support import DEVICES, KEY, RECEIVED, SENT, STATUS, USAGE, AsyncFakeTransport, FakeTransport, response

T = TypeVar("T")


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def test_sends_a_text_and_a_code() -> None:
    transport = AsyncFakeTransport(response(200, SENT), response(200, SENT))
    client = AsyncClient(KEY, transport=transport)

    result = run(client.send_sms("+14155550123", "Your table is ready.", default_country_code="+1"))
    assert result.message_id == "6ae7957285431f6150ce52fc"
    assert transport.sync.last.url == "https://smstunnel.io/api/v1/sms/send"
    assert transport.sync.last_json() == {
        "to": "+14155550123",
        "message": "Your table is ready.",
        "defaultCountryCode": "+1",
    }

    run(client.send_verification_code("+14155550123", "Code: 4821"))
    assert transport.sync.last.url == "https://smstunnel.io/api/v1/sms/send-2fa"


def test_reads_status_received_devices_and_usage() -> None:
    transport = AsyncFakeTransport(
        response(200, STATUS), response(200, RECEIVED), response(200, DEVICES), response(200, USAGE)
    )

    async def scenario() -> None:
        async with AsyncClient(KEY, "http://127.0.0.1:3101", transport=transport) as client:
            assert (await client.get_message_status("m")).status == "sent"
            assert len(await client.get_received_messages(limit=2)) == 2
            assert transport.sync.last.url == "http://127.0.0.1:3101/api/v1/sms/received?limit=2"
            assert (await client.get_devices()).online == 1
            assert (await client.get_usage()).plan == "pro"

    run(scenario())
    assert transport.closed is False, "a transport passed in stays open"


def test_raises_the_same_errors() -> None:
    transport = AsyncFakeTransport(
        response(401, {"message": "Invalid or expired API key"}),
        response(429, {"message": "Too Many Requests"}, {"Retry-After": "3"}),
    )
    client = AsyncClient(KEY, transport=transport)

    with pytest.raises(AuthenticationError):
        run(client.get_devices())
    with pytest.raises(RateLimitError) as caught:
        run(client.send_sms("+14155550123", "Hi"))
    assert caught.value.retry_after == 3.0


def test_validates_before_sending() -> None:
    transport = AsyncFakeTransport()
    client = AsyncClient(KEY, transport=transport)

    with pytest.raises(ValueError, match="empty"):
        run(client.send_sms("+14155550123", ""))
    assert transport.sync.requests == []


def test_runs_a_sync_transport_in_a_thread() -> None:
    sync = FakeTransport().respond(200, DEVICES)
    client = AsyncClient(KEY, transport=ThreadedAsyncTransport(sync))

    assert run(client.get_devices()).total == 2
    assert sync.last.url == "https://smstunnel.io/api/v1/devices"


def test_uses_httpx_when_it_is_installed() -> None:
    pytest.importorskip("httpx")

    async def scenario() -> None:
        client = AsyncClient(KEY)
        assert isinstance(client._transport, AsyncHttpxTransport)
        await client.aclose()

    run(scenario())


def test_falls_back_to_a_thread_without_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)

    client = AsyncClient(KEY)

    assert isinstance(client._transport, ThreadedAsyncTransport)
    run(client.aclose())


def test_never_shows_the_key() -> None:
    client = AsyncClient(KEY, transport=AsyncFakeTransport())

    assert KEY not in repr(client)
