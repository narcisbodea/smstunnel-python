"""Scheduled texts: ``send_sms(send_at=...)``, ``list_scheduled()``, ``get_scheduled()``, ``cancel_scheduled()``."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

import pytest

from smstunnel import (
    ApiError,
    AsyncClient,
    Client,
    PermissionDeniedError,
    RateLimitError,
    ScheduledText,
    ScheduledTexts,
    SendResult,
)

from .support import KEY, SENT, AsyncFakeTransport, FakeTransport, response

TO = "+14155550123"
TEXT = "Reminder: your appointment is tomorrow at 10:30."
ID = "6b0a1c2d3e4f5a6b7c8d9e0f"
UTC = timezone.utc

# One scheduled text as SMSTunnel answers it (SMSserver scheduled-texts.service.ts apiView).
ITEM: dict[str, Any] = {
    "id": ID,
    "to": TO,
    "message": TEXT,
    "sendAt": "2026-11-16T07:00:00.000Z",
    "status": "scheduled",
    "deviceId": None,
    "messageId": None,
    "error": None,
    "createdAt": "2026-11-15T10:12:44.120Z",
}
SCHEDULED: dict[str, Any] = {
    "success": True,
    "scheduled": True,
    "scheduledId": ID,
    "sendAt": "2026-11-16T07:00:00.000Z",
    "data": ITEM,
}


def client_with(*answers: Any) -> tuple[Client, FakeTransport]:
    transport = FakeTransport(*answers)
    return Client(KEY, transport=transport), transport


class TestSendAt:
    def test_an_aware_datetime_goes_out_with_its_own_offset_and_the_answer_is_a_scheduled_text(self) -> None:
        client, transport = client_with(response(200, SCHEDULED))
        when = datetime(2026, 11, 16, 9, 0, tzinfo=timezone(timedelta(hours=2)))

        text = client.send_sms(TO, TEXT, send_at=when)

        assert transport.last.method == "POST"
        assert transport.last.url == "https://smstunnel.io/api/v1/sms/send"
        assert transport.last_json() == {"to": TO, "message": TEXT, "sendAt": "2026-11-16T09:00:00+02:00"}
        assert isinstance(text, ScheduledText)
        assert text.id == ID
        assert text.to == TO
        assert text.message == TEXT
        assert text.send_at == datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
        assert text.send_at == when, "the same moment"
        assert text.status == ScheduledText.SCHEDULED
        assert text.device_id is None
        assert text.message_id is None
        assert text.error is None
        assert text.created_at == datetime(2026, 11, 15, 10, 12, 44, 120000, tzinfo=UTC)
        assert text.raw == ITEM
        assert text.replayed is False

    @pytest.mark.parametrize(
        ("when", "sent"),
        [
            pytest.param(datetime(2026, 11, 16, 7, 0, tzinfo=UTC), "2026-11-16T07:00:00+00:00", id="UTC"),
            pytest.param(
                datetime(2026, 11, 16, 2, 0, tzinfo=timezone(timedelta(hours=-5))),
                "2026-11-16T02:00:00-05:00",
                id="a negative offset",
            ),
            pytest.param(
                datetime(2026, 11, 16, 12, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
                "2026-11-16T12:30:00+05:30",
                id="a half-hour offset",
            ),
            pytest.param(
                datetime(2026, 11, 16, 7, 0, 0, 750123, tzinfo=UTC),
                "2026-11-16T07:00:00+00:00",
                id="fractions of a second dropped",
            ),
        ],
    )
    def test_every_aware_datetime_goes_out_as_its_isoformat_to_the_second(self, when: datetime, sent: str) -> None:
        client, transport = client_with(response(200, SCHEDULED))

        client.send_sms(TO, TEXT, send_at=when)

        assert transport.last_json()["sendAt"] == sent

    def test_a_naive_datetime_is_a_value_error_that_says_why(self) -> None:
        client, transport = client_with()

        with pytest.raises(ValueError, match="send_at must be timezone-aware") as caught:
            client.send_sms(TO, TEXT, send_at=datetime(2026, 11, 16, 9, 0))

        assert "the moment it means is not known" in str(caught.value)
        assert transport.requests == []

    def test_a_time_zone_without_an_offset_is_naive_too(self) -> None:
        class NoOffset(tzinfo):
            def utcoffset(self, dt: datetime | None) -> timedelta | None:
                return None

            def dst(self, dt: datetime | None) -> timedelta | None:
                return None

            def tzname(self, dt: datetime | None) -> str | None:
                return None

        client, transport = client_with()

        with pytest.raises(ValueError, match="timezone-aware"):
            client.send_sms(TO, TEXT, send_at=datetime(2026, 11, 16, 9, 0, tzinfo=NoOffset()))
        assert transport.requests == []

    @pytest.mark.parametrize(
        ("when", "sent"),
        [
            pytest.param("2026-11-16T07:00:00Z", "2026-11-16T07:00:00Z", id="UTC"),
            pytest.param("2026-11-16T09:00:00+02:00", "2026-11-16T09:00:00+02:00", id="an offset"),
            pytest.param("2026-11-16T02:00:00-05:00", "2026-11-16T02:00:00-05:00", id="a negative offset"),
            pytest.param("2026-11-16T07:00:00.250Z", "2026-11-16T07:00:00.250Z", id="milliseconds"),
            pytest.param("2026-11-16T09:00+02:00", "2026-11-16T09:00+02:00", id="no seconds"),
            pytest.param("  2026-11-16T07:00:00Z\n", "2026-11-16T07:00:00Z", id="spaces around"),
        ],
    )
    def test_an_iso_string_goes_out_as_it_is(self, when: str, sent: str) -> None:
        client, transport = client_with(response(200, SCHEDULED))

        text = client.send_sms(TO, TEXT, send_at=when)

        assert transport.last_json()["sendAt"] == sent
        assert text.id == ID

    @pytest.mark.parametrize(
        "when",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="spaces only"),
            pytest.param("2026-11-16", id="a date only"),
            pytest.param("2026-11-16T09:00:00", id="no offset"),
            pytest.param("2026-11-16 09:00:00+02:00", id="a space instead of T"),
            pytest.param("tomorrow 9am", id="words"),
            pytest.param("2026-11-16T07:00:00.123456Z", id="microseconds"),
            pytest.param("2026-11-16T09:00:00+0200", id="an offset without a colon"),
            pytest.param("2026-11-16t07:00:00z", id="lower case"),
            pytest.param(
                "2026-11-16T07:00:00Z".translate({ord(digit): 0x0660 + int(digit) for digit in "0123456789"}),
                id="digits that are not ASCII",
            ),
            pytest.param("1794812400", id="a timestamp"),
        ],
    )
    def test_a_string_smstunnel_cannot_take_raises_before_anything_is_sent(self, when: str) -> None:
        client, transport = client_with()

        with pytest.raises(ValueError, match="send_at must be an ISO 8601 date and time with its offset"):
            client.send_sms(TO, TEXT, send_at=when)
        assert transport.requests == []

    @pytest.mark.parametrize(
        "when", [date(2026, 11, 16), 1794812400, 1794812400.0], ids=lambda when: type(when).__name__
    )
    def test_anything_else_is_a_type_error(self, when: object) -> None:
        client, transport = client_with()

        with pytest.raises(TypeError, match="send_at must be a timezone-aware datetime or an ISO 8601 string"):
            client.send_sms(TO, TEXT, send_at=when)  # type: ignore[call-overload]
        assert transport.requests == []

    def test_without_send_at_the_answer_is_a_send_result_as_before(self) -> None:
        client, transport = client_with(response(200, SENT))

        result = client.send_sms(TO, TEXT, send_at=None)

        assert isinstance(result, SendResult)
        assert result.message_id == SENT["messageId"]
        assert "sendAt" not in transport.last_json()

    def test_every_other_argument_goes_out_with_send_at(self) -> None:
        client, transport = client_with(response(200, SCHEDULED))

        client.send_sms(
            "0721123456",
            TEXT,
            device_id="device-1",
            default_country_code="+40",
            idempotency_key="booking-881-reminder",
            send_at="2026-11-16T09:00:00+02:00",
        )

        assert transport.last.headers["Idempotency-Key"] == "booking-881-reminder"
        assert transport.last_json() == {
            "to": "0721123456",
            "message": TEXT,
            "deviceId": "device-1",
            "defaultCountryCode": "+40",
            "sendAt": "2026-11-16T09:00:00+02:00",
        }

    def test_an_answer_without_data_still_gives_the_id_and_the_time(self) -> None:
        answer = {"success": True, "scheduled": True, "scheduledId": ID, "sendAt": "2026-11-16T07:00:00.000Z"}
        client, _ = client_with(response(200, answer))

        text = client.send_sms(TO, TEXT, send_at="2026-11-16T07:00:00Z")

        assert text.id == ID
        assert text.send_at == datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
        assert text.status == ScheduledText.SCHEDULED

    def test_a_retry_with_the_same_key_gets_the_first_scheduled_text_back(self) -> None:
        client, _ = client_with(response(200, SCHEDULED, {"Idempotent-Replayed": "true"}))

        text = client.send_sms(TO, TEXT, idempotency_key="booking-881-reminder", send_at="2026-11-16T07:00:00Z")

        assert text.replayed is True, "this call scheduled no new text"
        assert text.id == ID

    def test_sign_in_codes_take_no_send_at(self) -> None:
        client, _ = client_with()

        with pytest.raises(TypeError):
            client.send_verification_code(TO, "Your code is 482913", send_at="2026-11-16T07:00:00Z")  # type: ignore[call-arg]


class TestListGetCancel:
    def test_list_asks_for_the_waiting_ones_by_default(self) -> None:
        page = {
            "items": [ITEM, {**ITEM, "id": "6b0a1c2d3e4f5a6b7c8d9e10", "deviceId": "device-1"}],
            "total": 2,
            "page": 1,
            "limit": 50,
            "waiting": 7,
            "max": 200,
        }
        client, transport = client_with(response(200, {"success": True, "data": page}))

        texts = client.list_scheduled()

        assert transport.last.method == "GET"
        assert transport.last.url == "https://smstunnel.io/api/v1/sms/scheduled?scope=waiting&page=1&limit=50"
        assert transport.last.body is None
        assert "Content-Type" not in transport.last.headers
        assert isinstance(texts, ScheduledTexts)
        assert len(texts) == 2
        assert (texts.total, texts.page, texts.limit) == (2, 1, 50)
        assert texts.waiting == 7, "texts waiting in the whole account"
        assert texts.max == 200, "the plan's cap"
        assert list(texts) == list(texts.items)
        assert texts.items[0].id == ID
        assert texts.items[1].device_id == "device-1"

    def test_list_reads_the_done_ones(self) -> None:
        page = {
            "items": [
                {**ITEM, "status": "sent", "messageId": "6b0a1c2d3e4f5a6b7c8d9eaa"},
                {**ITEM, "status": "failed", "error": "limit_reached"},
                {**ITEM, "status": "canceled"},
            ],
            "total": 23,
            "page": 2,
            "limit": 3,
            "waiting": 0,
            "max": 10,
        }
        client, transport = client_with(response(200, {"success": True, "data": page}))

        texts = client.list_scheduled(ScheduledTexts.DONE, page=2, limit=3)

        assert transport.last.url == "https://smstunnel.io/api/v1/sms/scheduled?scope=done&page=2&limit=3"
        sent, failed, canceled = texts
        assert sent.status == ScheduledText.SENT
        assert sent.message_id == "6b0a1c2d3e4f5a6b7c8d9eaa"
        assert failed.status == ScheduledText.FAILED
        assert failed.error == "limit_reached"
        assert canceled.status == ScheduledText.CANCELED
        assert texts.total == 23

    def test_list_refuses_an_unknown_scope(self) -> None:
        client, transport = client_with()

        with pytest.raises(ValueError, match="Unknown scope 'sent'"):
            client.list_scheduled("sent")  # type: ignore[arg-type]
        assert transport.requests == []

    def test_an_empty_list_has_no_items(self) -> None:
        empty = {"items": [], "total": 0, "page": 1, "limit": 50, "waiting": 0, "max": 10}
        client, _ = client_with(response(200, {"success": True, "data": empty}))

        assert len(client.list_scheduled()) == 0

    def test_get_reads_one_text(self) -> None:
        data = {**ITEM, "status": "sent", "messageId": "6b0a1c2d3e4f5a6b7c8d9eaa"}
        client, transport = client_with(response(200, {"success": True, "data": data}))

        text = client.get_scheduled(f" {ID} ")

        assert transport.last.method == "GET"
        assert transport.last.url == f"https://smstunnel.io/api/v1/sms/scheduled/{ID}"
        assert transport.last.body is None
        assert text.status == ScheduledText.SENT
        assert text.message_id == "6b0a1c2d3e4f5a6b7c8d9eaa", "for get_message_status()"

    def test_the_id_is_escaped_in_the_path(self) -> None:
        client, transport = client_with(response(200, {"success": True, "data": ITEM}))

        client.get_scheduled("../status/x?y=1")

        assert transport.last.url == "https://smstunnel.io/api/v1/sms/scheduled/..%2Fstatus%2Fx%3Fy%3D1"

    def test_cancel_sends_a_delete_without_a_body(self) -> None:
        client, transport = client_with(response(200, {"success": True, "data": {**ITEM, "status": "canceled"}}))

        text = client.cancel_scheduled(ID)

        request = transport.last
        assert request.method == "DELETE"
        assert request.url == f"https://smstunnel.io/api/v1/sms/scheduled/{ID}"
        assert request.body is None
        assert "Content-Type" not in request.headers
        assert "Idempotency-Key" not in request.headers
        assert request.headers["X-API-Key"] == KEY
        assert text.status == ScheduledText.CANCELED

    @pytest.mark.parametrize("call", ["get_scheduled", "cancel_scheduled"])
    @pytest.mark.parametrize("scheduled_id", ["", "   "])
    def test_an_empty_id_raises_before_anything_is_sent(self, call: str, scheduled_id: str) -> None:
        client, transport = client_with()

        with pytest.raises(ValueError, match="The scheduled text id is empty"):
            getattr(client, call)(scheduled_id)
        assert transport.requests == []


class TestErrors:
    @pytest.mark.parametrize(
        ("status", "code", "kind"),
        [
            (400, "SCHEDULE_TOO_SOON", ApiError),
            (400, "SCHEDULE_TOO_FAR", ApiError),
            (400, "SCHEDULE_INVALID_INPUT", ApiError),
            (400, "SCHEDULE_TEXT_TOO_LONG", ApiError),
            (400, "SCHEDULE_NUMBER_NOT_ALLOWED", ApiError),
            (400, "SCHEDULE_SEALED_ONLY_PHONE", ApiError),
            (404, "SCHEDULE_PHONE_NOT_FOUND", ApiError),
            (403, "SCHEDULE_LIMIT_REACHED", PermissionDeniedError),
        ],
    )
    def test_a_refused_scheduled_text_keeps_its_code(self, status: int, code: str, kind: type[ApiError]) -> None:
        labels = {400: "Bad Request", 403: "Forbidden", 404: "Not Found"}
        body: dict[str, Any] = {
            "statusCode": status,
            "error": labels[status],
            "code": code,
            "message": f"Words for {code}",
        }
        client, _ = client_with(response(status, body))

        with pytest.raises(ApiError) as caught:
            client.send_sms(TO, TEXT, send_at="2026-11-16T07:00:00Z")

        assert type(caught.value) is kind
        assert caught.value.status_code == status
        assert caught.value.error_code == code
        assert caught.value.message == f"Words for {code}"
        assert caught.value.body == body

    def test_a_full_plan_says_its_cap(self) -> None:
        body = {
            "statusCode": 403,
            "error": "Forbidden",
            "code": "SCHEDULE_LIMIT_REACHED",
            "message": "You have as many scheduled texts as your plan allows. Wait until some go out, or cancel one.",
            "max": 50,
        }
        client, _ = client_with(response(403, body))

        with pytest.raises(PermissionDeniedError) as caught:
            client.send_sms(TO, TEXT, send_at="2026-11-16T07:00:00Z")

        assert caught.value.body["max"] == 50
        assert caught.value.permission is None

    def test_too_many_requests_is_a_rate_limit(self) -> None:
        client, _ = client_with(
            response(429, {"statusCode": 429, "message": "Too Many Requests"}, {"Retry-After": "12"})
        )

        with pytest.raises(RateLimitError) as caught:
            client.send_sms(TO, TEXT, send_at="2026-11-16T07:00:00Z")

        assert caught.value.retry_after == 12.0

    def test_a_server_without_scheduled_texts_gives_its_own_400(self) -> None:
        # What SMSTunnel answered before scheduled texts: its validation refuses the unknown field.
        body = {"message": ["property sendAt should not exist"], "error": "Bad Request", "statusCode": 400}
        client, _ = client_with(response(400, body))

        with pytest.raises(ApiError) as caught:
            client.send_sms(TO, TEXT, send_at="2026-11-16T07:00:00Z")

        assert type(caught.value) is ApiError
        assert caught.value.status_code == 400
        assert caught.value.error_code is None
        assert caught.value.message == "property sendAt should not exist"

    def test_a_text_this_key_did_not_schedule_is_not_found(self) -> None:
        body = {"statusCode": 404, "error": "Not Found", "code": "SCHEDULE_NOT_FOUND", "message": "No such text."}
        client, _ = client_with(response(404, body))

        with pytest.raises(ApiError) as caught:
            client.get_scheduled(ID)

        assert caught.value.status_code == 404
        assert caught.value.error_code == "SCHEDULE_NOT_FOUND"

    def test_a_text_that_already_went_out_cannot_be_canceled(self) -> None:
        body = {
            "statusCode": 409,
            "error": "Conflict",
            "code": "SCHEDULE_NOT_EDITABLE",
            "message": "This text has already gone out or was canceled, or was scheduled through the API.",
        }
        client, _ = client_with(response(409, body))

        with pytest.raises(ApiError) as caught:
            client.cancel_scheduled(ID)

        assert type(caught.value) is ApiError
        assert caught.value.status_code == 409
        assert caught.value.error_code == "SCHEDULE_NOT_EDITABLE"
        assert caught.value.retry_after is None

    def test_listing_needs_the_read_permission(self) -> None:
        body = {"statusCode": 403, "code": "PERMISSION_REQUIRED", "permission": "sms:read", "message": "No."}
        client, _ = client_with(response(403, body))

        with pytest.raises(PermissionDeniedError) as caught:
            client.list_scheduled()

        assert caught.value.error_code == "PERMISSION_REQUIRED"
        assert caught.value.permission == "sms:read"


class TestAsyncClient:
    def test_schedules_lists_reads_and_cancels(self) -> None:
        page = {"items": [ITEM], "total": 1, "page": 1, "limit": 20, "waiting": 1, "max": 10}
        transport = AsyncFakeTransport(
            response(200, SCHEDULED),
            response(200, {"success": True, "data": page}),
            response(200, {"success": True, "data": ITEM}),
            response(200, {"success": True, "data": {**ITEM, "status": "canceled"}}),
        )
        client = AsyncClient(KEY, transport=transport)

        async def scenario() -> tuple[ScheduledText, ScheduledTexts, ScheduledText, ScheduledText]:
            when = datetime(2026, 11, 16, 9, 0, tzinfo=timezone(timedelta(hours=2)))
            scheduled = await client.send_sms(TO, TEXT, send_at=when)
            return (
                scheduled,
                await client.list_scheduled(limit=20),
                await client.get_scheduled(scheduled.id),
                await client.cancel_scheduled(scheduled.id),
            )

        scheduled, listed, read, canceled = asyncio.run(scenario())

        send, listing, get, cancel = transport.sync.requests
        assert send.method == "POST"
        assert transport.sync.requests[0].body == (
            b'{"to":"+14155550123","message":"' + TEXT.encode() + b'","sendAt":"2026-11-16T09:00:00+02:00"}'
        )
        assert listing.url == "https://smstunnel.io/api/v1/sms/scheduled?scope=waiting&page=1&limit=20"
        assert get.method == "GET"
        assert get.url.endswith(f"/api/v1/sms/scheduled/{ID}")
        assert cancel.method == "DELETE"
        assert cancel.body is None
        assert scheduled.id == ID
        assert len(listed) == 1
        assert read.id == ID
        assert canceled.status == ScheduledText.CANCELED

    def test_refuses_a_naive_datetime_and_marks_a_replay(self) -> None:
        transport = AsyncFakeTransport(response(200, SCHEDULED, {"Idempotent-Replayed": "true"}))
        client = AsyncClient(KEY, transport=transport)

        with pytest.raises(ValueError, match="timezone-aware"):
            asyncio.run(client.send_sms(TO, TEXT, send_at=datetime(2026, 11, 16, 9, 0)))
        assert transport.sync.requests == []

        text = asyncio.run(client.send_sms(TO, TEXT, idempotency_key="k-1", send_at="2026-11-16T07:00:00Z"))
        assert text.replayed is True

    def test_without_send_at_it_is_a_send_result(self) -> None:
        client = AsyncClient(KEY, transport=AsyncFakeTransport(response(200, SENT)))

        result = asyncio.run(client.send_sms(TO, TEXT))

        assert isinstance(result, SendResult)
        assert result.message_id == SENT["messageId"]
