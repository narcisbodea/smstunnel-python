"""``idempotency_key``: the ``Idempotency-Key`` header that makes a retried send safe."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from smstunnel import ApiError, AsyncClient, Client, SendResult, TransportError

from .support import KEY, SENT, AsyncFakeTransport, FakeTransport, response

TO = "+14155550123"
TEXT = "Your order #1042 has shipped."

# The server's answers (SMSserver public-api/idempotency/idempotency.service.ts).
REUSED = {
    "statusCode": 422,
    "error": "Unprocessable Entity",
    "code": "IDEMPOTENCY_KEY_REUSED",
    "message": (
        "This Idempotency-Key was already used in the last 24 hours for a different request (another endpoint, "
        "recipient, text, phone or country code). Use a new key for a new text."
    ),
}
IN_PROGRESS = {
    "statusCode": 409,
    "error": "Conflict",
    "code": "IDEMPOTENCY_KEY_IN_PROGRESS",
    "message": "A request with this Idempotency-Key is still being processed. Retry in a moment to get its result.",
}

Send = Callable[..., SendResult]


def send_sms(client: Client) -> Send:
    return client.send_sms


def send_verification_code(client: Client) -> Send:
    return lambda to, _text, **options: client.send_verification_code(to, "Your sign-in code is 482913", **options)


SENDS = [
    pytest.param(send_sms, "/api/v1/sms/send", id="send_sms"),
    pytest.param(send_verification_code, "/api/v1/sms/send-2fa", id="send_verification_code"),
]


@pytest.mark.parametrize(("pick", "path"), SENDS)
def test_the_key_goes_out_as_the_idempotency_key_header(pick: Callable[[Client], Send], path: str) -> None:
    transport = FakeTransport().respond(200, SENT)
    send = pick(Client(KEY, transport=transport))

    send(TO, TEXT, idempotency_key="order-1042-shipped")

    request = transport.last
    assert request.url == "https://smstunnel.io" + path
    assert request.headers["Idempotency-Key"] == "order-1042-shipped"
    assert "idempotencyKey" not in transport.last_json(), "a header, not a field of the body"
    assert request.headers["X-API-Key"] == KEY


@pytest.mark.parametrize(("pick", "path"), SENDS)
def test_no_key_no_header(pick: Callable[[Client], Send], path: str) -> None:
    transport = FakeTransport().respond(200, SENT).respond(200, SENT)
    send = pick(Client(KEY, transport=transport))

    send(TO, TEXT)
    send(TO, TEXT, idempotency_key=None)

    assert len(transport.requests) == 2
    for request in transport.requests:
        assert request.url == "https://smstunnel.io" + path
        assert "idempotency-key" not in {name.lower() for name in request.headers}


def test_a_retry_after_no_answer_sends_the_same_key_and_gets_the_first_answer() -> None:
    # The first try reached SMSTunnel, but its answer was lost; the retry gets the first answer back.
    replayed = response(200, SENT, {"Idempotent-Replayed": "true"})
    transport = FakeTransport(TransportError("Could not reach SMSTunnel: no answer within 30 s."), replayed)
    client = Client(KEY, transport=transport)
    key = str(uuid.uuid4())

    with pytest.raises(TransportError):
        client.send_sms(TO, TEXT, idempotency_key=key)
    assert len(transport.requests) == 1, "the client never retries on its own"

    result = client.send_sms(TO, TEXT, idempotency_key=key)

    first, retry = transport.requests
    assert first.headers["Idempotency-Key"] == retry.headers["Idempotency-Key"] == key
    assert first.body == retry.body
    assert result.message_id == SENT["messageId"]


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(TransportError("Could not reach SMSTunnel: no answer within 30 s."), id="no answer"),
        pytest.param(response(409, IN_PROGRESS, {"Retry-After": "1"}), id="409 in progress"),
        pytest.param(response(429, {"message": "Too Many Requests"}, {"Retry-After": "5"}), id="429"),
        pytest.param(response(500, {"statusCode": 500, "message": "Internal server error"}), id="500"),
    ],
)
def test_the_client_never_retries_on_its_own(answer: Any) -> None:
    transport = FakeTransport(answer)
    client = Client(KEY, transport=transport)

    with pytest.raises((TransportError, ApiError)):
        client.send_sms(TO, TEXT, idempotency_key="order-1042-shipped")

    assert len(transport.requests) == 1


@pytest.mark.parametrize(("pick", "path"), SENDS)
def test_a_key_used_for_another_text_is_a_422_with_its_code(pick: Callable[[Client], Send], path: str) -> None:
    transport = FakeTransport().respond(422, REUSED)
    send = pick(Client(KEY, transport=transport))

    with pytest.raises(ApiError) as caught:
        send(TO, "Your order #1043 has shipped.", idempotency_key="order-1042-shipped")

    error = caught.value
    assert type(error) is ApiError
    assert error.status_code == 422
    assert error.error_code == "IDEMPOTENCY_KEY_REUSED"
    assert error.message == REUSED["message"]
    assert error.body == REUSED
    assert error.retry_after is None


def test_a_key_still_in_progress_is_a_409_that_says_when_to_try_again() -> None:
    client = Client(KEY, transport=FakeTransport().respond(409, IN_PROGRESS, {"Retry-After": "1"}))

    with pytest.raises(ApiError) as caught:
        client.send_sms(TO, TEXT, idempotency_key="order-1042-shipped")

    assert caught.value.status_code == 409
    assert caught.value.error_code == "IDEMPOTENCY_KEY_IN_PROGRESS"
    assert caught.value.retry_after == 1.0


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("a", id="one character"),
        pytest.param("order-1042-shipped", id="own id"),
        pytest.param(str(uuid.uuid4()), id="uuid"),
        pytest.param("order 1042 shipped", id="inner spaces"),
        pytest.param("!" + "x" * 253 + "~", id="255 characters"),
        pytest.param('"quoted"', id="quotes"),
    ],
)
def test_a_valid_key_is_sent_as_it_is(key: str) -> None:
    transport = FakeTransport().respond(200, SENT)

    Client(KEY, transport=transport).send_sms(TO, TEXT, idempotency_key=key)

    assert transport.last.headers["Idempotency-Key"] == key


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="spaces only"),
        pytest.param(" order-1042", id="leading space"),
        pytest.param("order-1042 ", id="trailing space"),
        pytest.param("x" * 256, id="256 characters"),
        pytest.param("comandă-1042", id="not ascii"),
        pytest.param("order\t1042", id="tab"),
        pytest.param("order-1042\n", id="trailing newline"),
        pytest.param("order\r\nX-Other: 1", id="header injection"),
        pytest.param("order\x7f", id="delete character"),
    ],
)
@pytest.mark.parametrize(("pick", "path"), SENDS)
def test_a_key_smstunnel_cannot_take_raises_before_anything_is_sent(
    pick: Callable[[Client], Send], path: str, key: str
) -> None:
    transport = FakeTransport()
    send = pick(Client(KEY, transport=transport))

    with pytest.raises(ValueError, match="idempotency_key must be 1 to 255 printable ASCII characters"):
        send(TO, TEXT, idempotency_key=key)

    assert transport.requests == []


@pytest.mark.parametrize("key", [1042, b"order-1042", uuid.uuid4(), ["order-1042"]], ids=lambda key: type(key).__name__)
def test_a_key_that_is_not_a_str_is_a_type_error(key: object) -> None:
    transport = FakeTransport()
    client = Client(KEY, transport=transport)

    with pytest.raises(TypeError, match="idempotency_key must be a str"):
        client.send_sms(TO, TEXT, idempotency_key=key)  # type: ignore[arg-type]

    assert transport.requests == []


class TestAsyncClient:
    def test_sends_the_key_on_both_send_calls_and_none_without_it(self) -> None:
        transport = AsyncFakeTransport(response(200, SENT), response(200, SENT), response(200, SENT))
        client = AsyncClient(KEY, transport=transport)

        asyncio.run(client.send_sms(TO, TEXT, idempotency_key="order-1042-shipped"))
        asyncio.run(client.send_verification_code(TO, "Your sign-in code is 482913", idempotency_key="sign-in-77"))
        asyncio.run(client.send_sms(TO, TEXT))

        first, second, third = transport.sync.requests
        assert first.url.endswith("/api/v1/sms/send")
        assert first.headers["Idempotency-Key"] == "order-1042-shipped"
        assert second.url.endswith("/api/v1/sms/send-2fa")
        assert second.headers["Idempotency-Key"] == "sign-in-77"
        assert "Idempotency-Key" not in third.headers

    def test_retries_with_the_same_key(self) -> None:
        transport = AsyncFakeTransport(TransportError("no answer"), response(200, SENT))
        client = AsyncClient(KEY, transport=transport)

        async def scenario() -> SendResult:
            for attempt in range(2):
                try:
                    return await client.send_sms(TO, TEXT, idempotency_key="order-1042-shipped")
                except TransportError:
                    if attempt == 1:
                        raise
            raise AssertionError("unreachable")

        assert asyncio.run(scenario()).message_id == SENT["messageId"]
        assert [request.headers["Idempotency-Key"] for request in transport.sync.requests] == ["order-1042-shipped"] * 2

    def test_validates_the_key_before_sending(self) -> None:
        transport = AsyncFakeTransport()
        client = AsyncClient(KEY, transport=transport)

        with pytest.raises(ValueError, match="idempotency_key"):
            asyncio.run(client.send_sms(TO, TEXT, idempotency_key=" "))
        with pytest.raises(TypeError, match="idempotency_key"):
            asyncio.run(client.send_verification_code(TO, "Code 482913", idempotency_key=42))  # type: ignore[arg-type]

        assert transport.sync.requests == []

    def test_a_reused_key_is_a_422_api_error_with_its_code(self) -> None:
        client = AsyncClient(KEY, transport=AsyncFakeTransport(response(422, REUSED)))

        with pytest.raises(ApiError) as caught:
            asyncio.run(client.send_sms(TO, TEXT, idempotency_key="order-1042-shipped"))

        assert caught.value.status_code == 422
        assert caught.value.error_code == "IDEMPOTENCY_KEY_REUSED"
