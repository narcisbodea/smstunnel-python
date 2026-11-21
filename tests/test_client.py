from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

import smstunnel
from smstunnel import (
    ApiError,
    AuthenticationError,
    Client,
    MessageStatus,
    PermissionDeniedError,
    RateLimitError,
    TransportError,
)

from .support import DEVICES, KEY, RECEIVED, SENT, STATUS, USAGE, FakeTransport, response


def client_with(*answers: object, base_url: str = "https://smstunnel.io") -> tuple[Client, FakeTransport]:
    transport = FakeTransport()
    for answer in answers:
        if isinstance(answer, BaseException):
            transport.answers.append(answer)
        else:
            assert isinstance(answer, dict)
            transport.respond(200, answer)
    return Client(KEY, base_url, transport=transport), transport


class TestSendSms:
    def test_posts_one_text_with_the_key_and_reads_the_result(self) -> None:
        client, transport = client_with(SENT)

        result = client.send_sms("+14155550123", "Your order #1042 has shipped.")

        request = transport.last
        assert request.method == "POST"
        assert request.url == "https://smstunnel.io/api/v1/sms/send"
        assert request.headers["X-API-Key"] == KEY
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["Accept"] == "application/json"
        assert request.headers["User-Agent"].startswith(f"smstunnel-python/{smstunnel.__version__} Python/")
        assert request.timeout == 30.0
        assert transport.last_json() == {"to": "+14155550123", "message": "Your order #1042 has shipped."}

        assert result.message_id == "6ae7957285431f6150ce52fc"
        assert result.queued is False
        assert result.branded is False
        assert result.remaining == {"clean": 95, "total": "unlimited"}
        assert result.raw["success"] is True

    def test_sends_the_optional_fields_trimmed(self) -> None:
        client, transport = client_with(SENT)

        client.send_sms(" 0712345678 ", "Hi", device_id=" 6ae660e96609f21a1184a210 ", default_country_code=" +40 ")

        assert transport.last_json() == {
            "to": "0712345678",
            "message": "Hi",
            "deviceId": "6ae660e96609f21a1184a210",
            "defaultCountryCode": "+40",
        }

    def test_leaves_out_blank_optional_fields(self) -> None:
        client, transport = client_with(SENT)

        client.send_sms("+14155550123", "Hi", device_id="  ", default_country_code="")

        assert set(transport.last_json()) == {"to", "message"}

    def test_keeps_the_text_as_it_is_in_utf8(self) -> None:
        client, transport = client_with(SENT)
        text = "Ți-am lăsat cheia la vecini 🙂\n  — Ana"

        client.send_sms("+14155550123", text)

        body = transport.last.body
        assert body is not None
        assert "Ți-am".encode() in body
        assert json.loads(body)["message"] == text

    def test_reads_a_queued_text(self) -> None:
        queued = {"success": True, "messageId": "m1", "queued": True, "data": {"messageId": "m1", "queued": True}}
        client, _ = client_with(queued)

        assert client.send_sms("+14155550123", "Hi").queued is True

    def test_reads_the_id_from_data_when_the_top_level_has_none(self) -> None:
        client, _ = client_with({"success": True, "data": {"messageId": "m2", "branded": True}})

        result = client.send_sms("+14155550123", "Hi")

        assert result.message_id == "m2"
        assert result.branded is True

    @pytest.mark.parametrize(("to", "message"), [("", "Hi"), ("   ", "Hi"), ("+14155550123", ""), ("+1415", "  \n")])
    def test_refuses_an_empty_number_or_text_before_sending(self, to: str, message: str) -> None:
        client, transport = client_with()

        with pytest.raises(ValueError, match="empty"):
            client.send_sms(to, message)
        assert transport.requests == []


class TestSendVerificationCode:
    def test_posts_to_the_code_endpoint(self) -> None:
        client, transport = client_with(SENT)

        result = client.send_verification_code("+14155550123", "Your sign-in code is 482913", device_id="d1")

        assert transport.last.url == "https://smstunnel.io/api/v1/sms/send-2fa"
        assert transport.last_json() == {
            "to": "+14155550123",
            "message": "Your sign-in code is 482913",
            "deviceId": "d1",
        }
        assert result.message_id == "6ae7957285431f6150ce52fc"

    def test_a_text_without_a_code_is_a_400(self) -> None:
        transport = FakeTransport().respond(
            400, {"message": "No valid verification code found", "error": "Bad Request", "statusCode": 400}
        )
        client = Client(KEY, transport=transport)

        with pytest.raises(ApiError) as caught:
            client.send_verification_code("+14155550123", "No code here")

        assert caught.value.status_code == 400
        assert str(caught.value) == "No valid verification code found"
        assert type(caught.value) is ApiError


class TestMessageStatus:
    def test_reads_the_status(self) -> None:
        client, transport = client_with(STATUS)

        status = client.get_message_status("6ae7957285431f6150ce52fc")

        assert transport.last.method == "GET"
        assert transport.last.url == "https://smstunnel.io/api/v1/sms/status/6ae7957285431f6150ce52fc"
        assert transport.last.body is None
        assert "Content-Type" not in transport.last.headers
        assert status.id == "6ae7957285431f6150ce52fc"
        assert status.phone_number == "+14155550123"
        assert status.status == MessageStatus.SENT
        assert status.sent_at == datetime(2026, 11, 1, 19, 39, 30, 329000, tzinfo=timezone.utc)
        assert status.delivered_at is None
        assert status.error_message is None
        assert status.is_final is True

    def test_escapes_the_id_in_the_path(self) -> None:
        client, transport = client_with(STATUS)

        client.get_message_status("../devices?x=1")

        assert transport.last.url == "https://smstunnel.io/api/v1/sms/status/..%2Fdevices%3Fx%3D1"

    def test_an_unconfirmed_text_is_final(self) -> None:
        data = {
            "id": "m",
            "phoneNumber": "+1",
            "status": "failed",
            "unconfirmed": True,
            "deliveryStatus": "unconfirmed",
        }
        client, _ = client_with({"success": True, "data": data})

        status = client.get_message_status("m")

        assert status.status == MessageStatus.FAILED
        assert status.unconfirmed is True
        assert status.delivery_status == MessageStatus.UNCONFIRMED
        assert status.is_final is True

    @pytest.mark.parametrize(
        ("state", "final"),
        [
            ("pending", False),
            ("sent", True),
            ("delivered", True),
            ("failed", True),
            ("unconfirmed", True),
            ("queued", False),
            ("read-by-martians", False),
        ],
    )
    def test_is_final_for_every_state(self, state: str, final: bool) -> None:
        assert MessageStatus(id="m", phone_number="+1", status=state).is_final is final
        assert MessageStatus(id="m", phone_number="+1", status="pending", delivery_status=state).is_final is final

    def test_refuses_an_empty_id(self) -> None:
        client, transport = client_with()

        with pytest.raises(ValueError, match="empty"):
            client.get_message_status(" ")
        assert transport.requests == []


class TestReceivedMessages:
    def test_reads_a_page(self) -> None:
        client, transport = client_with(RECEIVED)

        page = client.get_received_messages()

        assert transport.last.url == "https://smstunnel.io/api/v1/sms/received"
        assert len(page) == 2
        assert page.total == 7
        assert page.limit == 2
        assert page.has_more is True
        first, second = page
        assert first.sender == "+40799000222"
        assert first.message == "Is the shop open on Sunday?"
        assert first.received_at == datetime(2026, 10, 31, 21, 43, 10, 489000, tzinfo=timezone.utc)
        assert first.device_name == "Store phone"
        assert first.sim_slot == 1
        assert second.message is None
        assert second.is_encrypted is True
        assert second.encrypted_payload == "v1:abc"

    def test_sends_the_filters(self) -> None:
        client, transport = client_with(RECEIVED)
        since = datetime(2026, 11, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))

        client.get_received_messages(limit=20, page=2, since=since, phone="+40 712 345 678", device_id="d1")

        query = parse_qs(urlsplit(transport.last.url).query)
        assert query == {
            "limit": ["20"],
            "page": ["2"],
            "since": ["2026-11-01T10:00:00.000Z"],
            "phone": ["+40 712 345 678"],
            "deviceId": ["d1"],
        }
        assert "%2B40%20712" in transport.last.url

    def test_takes_since_as_text_and_a_naive_datetime_as_local_time(self) -> None:
        client, transport = client_with(RECEIVED, RECEIVED)

        client.get_received_messages(since="2026-11-01T00:00:00Z")
        assert parse_qs(urlsplit(transport.last.url).query) == {"since": ["2026-11-01T00:00:00Z"]}

        naive = datetime(2026, 11, 1, 9, 30)
        client.get_received_messages(since=naive)
        sent = parse_qs(urlsplit(transport.last.url).query)["since"][0]
        assert datetime.fromisoformat(sent.replace("Z", "+00:00")) == naive.astimezone(timezone.utc)

    def test_leaves_out_empty_filters(self) -> None:
        client, transport = client_with(RECEIVED)

        client.get_received_messages(since="  ", phone="", device_id=None)

        assert transport.last.url == "https://smstunnel.io/api/v1/sms/received"

    def test_a_key_without_the_inbox_permission_gets_a_permission_error(self) -> None:
        transport = FakeTransport().respond(
            403,
            {
                "statusCode": 403,
                "error": "Forbidden",
                "message": "This API key may not read received texts.",
                "code": "PERMISSION_REQUIRED",
                "permission": "sms:inbox",
            },
        )
        client = Client(KEY, transport=transport)

        with pytest.raises(PermissionDeniedError) as caught:
            client.get_received_messages()

        assert caught.value.status_code == 403
        assert caught.value.error_code == "PERMISSION_REQUIRED"
        assert caught.value.permission == "sms:inbox"
        assert str(caught.value) == "This API key may not read received texts."


class TestDevicesAndUsage:
    def test_reads_the_phones(self) -> None:
        client, transport = client_with(DEVICES)

        phones = client.get_devices()

        assert transport.last.url == "https://smstunnel.io/api/v1/devices"
        assert phones.total == 2
        assert phones.online == 1
        assert [phone.name for phone in phones] == ["Store phone", "Old Samsung"]
        found = phones.find("6ae660e96609f21a1184a210")
        assert found is not None
        assert found.is_online is True
        assert found.last_seen_at == datetime(2026, 11, 1, 20, 51, 9, 526000, tzinfo=timezone.utc)
        assert phones.find("nope") is None

    def test_counts_online_phones_when_the_answer_does_not(self) -> None:
        devices = DEVICES["data"]["devices"]  # type: ignore[index]
        client, _ = client_with({"success": True, "data": {"devices": devices}})

        phones = client.get_devices()

        assert (phones.online, phones.total) == (1, 2)

    def test_reads_the_usage(self) -> None:
        client, transport = client_with(USAGE)

        usage = client.get_usage()

        assert transport.last.url == "https://smstunnel.io/api/v1/account/usage"
        assert usage.plan == "pro"
        assert usage.account_type == "standard"
        assert usage.usage == {"smsClean": 108, "smsBranded": 0, "twoFaClean": 45}
        assert usage.limits == {"smsClean": "unlimited", "twoFaClean": 100}
        assert usage.remaining["twoFaClean"] == 55
        assert usage.enterprise is None

    def test_reads_the_company_of_a_company_client(self) -> None:
        data = {**USAGE["data"], "enterprise": {"id": "e1", "name": "Acme Clinics"}}  # type: ignore[dict-item]
        client, _ = client_with({"success": True, "data": data})

        assert client.get_usage().enterprise == {"id": "e1", "name": "Acme Clinics"}


class TestErrors:
    def test_a_wrong_key_is_an_authentication_error(self) -> None:
        body = {"message": "Invalid or expired API key", "error": "Unauthorized", "statusCode": 401}
        client = Client(KEY, transport=FakeTransport().respond(401, body))

        with pytest.raises(AuthenticationError) as caught:
            client.get_devices()

        assert caught.value.status_code == 401
        assert caught.value.message == "Invalid or expired API key"
        assert caught.value.body == body

    def test_a_rate_limit_says_how_long_to_wait(self) -> None:
        body = {"statusCode": 429, "message": "ThrottlerException: Too Many Requests"}
        client = Client(KEY, transport=FakeTransport().respond(429, body, {"Retry-After": "42"}))

        with pytest.raises(RateLimitError) as caught:
            client.send_sms("+14155550123", "Hi")

        assert caught.value.retry_after == 42.0
        assert isinstance(caught.value, ApiError)

    def test_a_server_error_page_becomes_its_words(self) -> None:
        page = (
            "<html><head><title>503 Service Unavailable</title></head>"
            "<body><h1>503 Service Unavailable</h1></body></html>"
        )
        client = Client(KEY, transport=FakeTransport().respond(503, page))

        with pytest.raises(ApiError) as caught:
            client.get_usage()

        assert caught.value.status_code == 503
        assert caught.value.message == "503 Service Unavailable 503 Service Unavailable"
        assert caught.value.body == page

    def test_success_false_in_a_200_is_an_error(self) -> None:
        client = Client(KEY, transport=FakeTransport().respond(200, {"success": False, "error": "Device offline"}))

        with pytest.raises(ApiError, match="Device offline") as caught:
            client.send_sms("+14155550123", "Hi")
        assert caught.value.status_code == 200

    def test_an_answer_that_is_not_json_is_an_error(self) -> None:
        client = Client(KEY, transport=FakeTransport().respond(200, "<html>Welcome</html>"))

        with pytest.raises(ApiError, match="not JSON"):
            client.get_devices()

    def test_an_empty_answer_is_an_error(self) -> None:
        client = Client(KEY, transport=FakeTransport().respond(200, None))

        with pytest.raises(ApiError, match="not JSON"):
            client.get_devices()

    def test_a_redirect_says_where_to_point_the_client(self) -> None:
        answer = response(301, None, {"Location": "https://www.smstunnel.io/api/v1/devices"})
        client = Client(KEY, transport=FakeTransport(answer))

        with pytest.raises(ApiError) as caught:
            client.get_devices()

        assert caught.value.status_code == 301
        assert "https://www.smstunnel.io/api/v1/devices" in caught.value.message
        assert "base URL" in caught.value.message

    def test_a_transport_error_comes_through(self) -> None:
        client = Client(KEY, transport=FakeTransport(TransportError("Could not reach SMSTunnel")))

        with pytest.raises(TransportError):
            client.get_devices()


class TestConfiguration:
    def test_points_at_another_server_without_a_trailing_slash(self) -> None:
        client, transport = client_with(DEVICES, base_url=" http://127.0.0.1:3101/ ")

        client.get_devices()

        assert client.base_url == "http://127.0.0.1:3101"
        assert transport.last.url == "http://127.0.0.1:3101/api/v1/devices"

    def test_keeps_a_path_prefix(self) -> None:
        client, transport = client_with(DEVICES, base_url="https://example.com/smstunnel/")

        client.get_devices()

        assert transport.last.url == "https://example.com/smstunnel/api/v1/devices"

    @pytest.mark.parametrize("base_url", ["smstunnel.io", "ftp://smstunnel.io", "https://", "https://a b.io", ""])
    def test_refuses_a_base_url_that_is_not_http(self, base_url: str) -> None:
        with pytest.raises(ValueError, match="base URL"):
            Client(KEY, base_url)

    @pytest.mark.parametrize("api_key", ["", "   "])
    def test_needs_a_key(self, api_key: str) -> None:
        with pytest.raises(ValueError, match="API key is required"):
            Client(api_key)

    def test_trims_the_key(self) -> None:
        transport = FakeTransport().respond(200, DEVICES)

        Client(f"  {KEY}\n", transport=transport).get_devices()

        assert transport.last.headers["X-API-Key"] == KEY

    @pytest.mark.parametrize("timeout", [0, -1, float("nan")])
    def test_refuses_a_timeout_that_is_not_positive(self, timeout: float) -> None:
        with pytest.raises(ValueError, match="timeout"):
            Client(KEY, timeout=timeout)

    def test_hands_the_timeout_to_the_transport(self) -> None:
        transport = FakeTransport().respond(200, DEVICES)

        Client(KEY, timeout=5, transport=transport).get_devices()

        assert transport.last.timeout == 5.0

    def test_never_shows_the_key(self) -> None:
        client = Client(KEY)

        assert KEY not in repr(client)
        assert "smstunnel.io" in repr(client)

    def test_closes_only_a_transport_it_made(self) -> None:
        transport = FakeTransport()
        with Client(KEY, transport=transport):
            pass
        assert transport.closed is False

        with Client(KEY) as client:
            assert client.base_url == "https://smstunnel.io"
