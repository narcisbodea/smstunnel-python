"""Against a real SMSTunnel test server, never production. Skipped unless SMSTUNNEL_E2E_KEY_FILE is set.

SMSTUNNEL_E2E_KEY_FILE=/path/key.json   {"serverUrl": "http://127.0.0.1:3101", "apiKey": "...", "deviceId": "..."}
                                        a test key with sms:send, sms:read and sms:inbox
SMSTUNNEL_E2E_MONGO_URL=mongodb://...   optional: the test server's database, to check each text was stored
SMSTUNNEL_E2E_TO=+14155550123           optional: the recipient
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

import pytest

from smstunnel import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    MessageStatus,
    RateLimitError,
)

pytestmark = pytest.mark.e2e

KEY_FILE = os.environ.get("SMSTUNNEL_E2E_KEY_FILE", "")
if not KEY_FILE:
    pytest.skip("Set SMSTUNNEL_E2E_KEY_FILE to run against a test server.", allow_module_level=True)

T = TypeVar("T")


class Settings:
    def __init__(self) -> None:
        data = json.loads(Path(KEY_FILE).read_text(encoding="utf-8"))
        self.server_url: str = data["serverUrl"]
        self.api_key: str = data["apiKey"]
        self.device_id: str | None = data.get("deviceId")
        self.to = os.environ.get("SMSTUNNEL_E2E_TO", "+14155550123")
        self.mongo_url = os.environ.get("SMSTUNNEL_E2E_MONGO_URL", "")

        host = urlsplit(self.server_url).hostname or ""
        if host == "smstunnel.io" or host.endswith(".smstunnel.io"):
            pytest.fail("The end-to-end tests never run against production. Point the key file at a test server.")


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def client(settings: Settings) -> Iterator[Client]:
    with Client(settings.api_key, settings.server_url) as client:
        yield client


@pytest.fixture(scope="module")
def stored(settings: Settings) -> Iterator[Callable[[str], dict[str, Any]] | None]:
    """Reads a text from the test server's database, when SMSTUNNEL_E2E_MONGO_URL is set."""
    if not settings.mongo_url:
        yield None
        return
    pymongo = pytest.importorskip("pymongo")
    bson = pytest.importorskip("bson")
    mongo = pymongo.MongoClient(settings.mongo_url, serverSelectionTimeoutMS=5000)
    messages = mongo.get_default_database()["messages"]

    def read(message_id: str) -> dict[str, Any]:
        document = messages.find_one({"_id": bson.ObjectId(message_id)})
        assert document is not None, f"Text {message_id} is not in the database."
        return dict(document)

    try:
        yield read
    finally:
        mongo.close()


def retry_on_rate_limit(call: Callable[[], T]) -> T:
    """The test server is shared, so one 429 is waited out."""
    try:
        return call()
    except RateLimitError as error:
        time.sleep(min(60.0, max(1.0, error.retry_after or 15.0)))
        return call()


def wait_until_sent(client: Client, message_id: str) -> MessageStatus:
    deadline = time.monotonic() + 30
    status = client.get_message_status(message_id)
    while status.status == MessageStatus.PENDING and time.monotonic() < deadline:
        time.sleep(0.5)
        status = client.get_message_status(message_id)
    return status


def check_stored(
    stored: Callable[[str], dict[str, Any]] | None, message_id: str, to: str, text: str, device_id: str | None
) -> None:
    if stored is None:
        return
    document = stored(message_id)
    assert document["type"] == "outgoing"
    assert document["phoneNumber"] == to
    assert document["message"] == text
    assert document["status"] in ("sent", "delivered")
    if device_id:
        assert str(document["deviceId"]) == device_id


def test_the_test_phone_is_linked_and_online(client: Client, settings: Settings) -> None:
    phones = client.get_devices()

    assert phones.total > 0
    assert phones.online > 0, "No phone is online on the test server."
    if settings.device_id:
        phone = phones.find(settings.device_id)
        assert phone is not None, "The test phone is not linked to this key's account."
        assert phone.is_online, "The test phone is offline."
        assert phone.last_seen_at is not None


def test_usage_reads_the_plan(client: Client) -> None:
    usage = client.get_usage()

    assert usage.plan
    assert "smsClean" in usage.usage


def test_a_text_reaches_the_phone_and_is_sent(
    client: Client, settings: Settings, stored: Callable[[str], dict[str, Any]] | None
) -> None:
    text = f"SMSTunnel Python end-to-end test {secrets.token_hex(4)}"

    result = retry_on_rate_limit(lambda: client.send_sms(settings.to, text, device_id=settings.device_id))

    assert len(result.message_id) == 24
    assert result.queued is False, "The phone was offline, so the text was queued."
    status = wait_until_sent(client, result.message_id)
    assert status.id == result.message_id
    assert status.phone_number == settings.to
    assert status.status in (MessageStatus.SENT, MessageStatus.DELIVERED)
    assert status.sent_at is not None
    check_stored(stored, result.message_id, settings.to, text, settings.device_id)


def test_a_local_number_is_completed_with_the_country_code(
    client: Client, settings: Settings, stored: Callable[[str], dict[str, Any]] | None
) -> None:
    text = f"SMSTunnel Python country code test {secrets.token_hex(4)}"

    result = retry_on_rate_limit(
        lambda: client.send_sms("4155550199", text, device_id=settings.device_id, default_country_code="+1")
    )

    status = wait_until_sent(client, result.message_id)
    assert status.phone_number == "+14155550199"
    check_stored(stored, result.message_id, "+14155550199", text, settings.device_id)


def test_a_verification_code_is_sent(
    client: Client, settings: Settings, stored: Callable[[str], dict[str, Any]] | None
) -> None:
    text = f"Your SMSTunnel Python test code is {secrets.randbelow(900000) + 100000}"

    result = retry_on_rate_limit(lambda: client.send_verification_code(settings.to, text, device_id=settings.device_id))

    status = wait_until_sent(client, result.message_id)
    assert status.phone_number == settings.to
    assert status.status in (MessageStatus.SENT, MessageStatus.DELIVERED)
    check_stored(stored, result.message_id, settings.to, text, settings.device_id)


def test_a_verification_text_without_a_code_is_refused(client: Client, settings: Settings) -> None:
    with pytest.raises(ApiError) as caught:
        retry_on_rate_limit(lambda: client.send_verification_code(settings.to, "There is no code in this text"))

    assert caught.value.status_code == 400
    assert "code" in caught.value.message


def test_received_texts_can_be_read(client: Client) -> None:
    page = client.get_received_messages(limit=5)

    assert page.limit == 5
    assert page.total >= len(page)
    for text in page:
        assert text.sender
        assert text.received_at is not None


def test_a_wrong_key_is_an_authentication_error(settings: Settings) -> None:
    with pytest.raises(AuthenticationError) as caught:
        Client("sk_live_this_key_does_not_exist_0000000000", settings.server_url).get_devices()

    assert caught.value.status_code == 401
    assert caught.value.message == "Invalid or expired API key"


def test_an_unknown_message_is_not_found(client: Client) -> None:
    with pytest.raises(ApiError) as caught:
        client.get_message_status("000000000000000000000000")

    assert caught.value.status_code == 404


def test_the_async_client_sends_a_text(
    client: Client, settings: Settings, stored: Callable[[str], dict[str, Any]] | None
) -> None:
    text = f"SMSTunnel Python async end-to-end test {secrets.token_hex(4)}"

    async def send() -> str:
        async with AsyncClient(settings.api_key, settings.server_url) as async_client:
            try:
                result = await async_client.send_sms(settings.to, text, device_id=settings.device_id)
            except RateLimitError as error:
                await asyncio.sleep(min(60.0, max(1.0, error.retry_after or 15.0)))
                result = await async_client.send_sms(settings.to, text, device_id=settings.device_id)
            assert (await async_client.get_devices()).online > 0
            return result.message_id

    message_id = asyncio.run(send())

    status = wait_until_sent(client, message_id)
    assert status.status in (MessageStatus.SENT, MessageStatus.DELIVERED)
    check_stored(stored, message_id, settings.to, text, settings.device_id)
