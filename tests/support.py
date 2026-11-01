"""Fake transports that answer from a queue and remember every request."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from smstunnel.transport import HttpRequest, HttpResponse

KEY = "sk_live_test_0123456789abcdef0123456789abcd"


def response(status: int, body: Any = None, headers: Mapping[str, str] | None = None) -> HttpResponse:
    """A dict or list is sent as JSON, a string as it is, ``None`` as an empty body."""
    if isinstance(body, (dict, list)):
        text = json.dumps(body)
    elif body is None:
        text = ""
    else:
        text = str(body)
    return HttpResponse(status, text, {name.lower(): value for name, value in (headers or {}).items()})


class FakeTransport:
    def __init__(self, *answers: HttpResponse | BaseException) -> None:
        self.answers: list[HttpResponse | BaseException] = list(answers)
        self.requests: list[HttpRequest] = []
        self.closed = False

    def respond(self, status: int, body: Any = None, headers: Mapping[str, str] | None = None) -> FakeTransport:
        self.answers.append(response(status, body, headers))
        return self

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.answers:
            raise AssertionError(f"No answer queued for {request.method} {request.url}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def close(self) -> None:
        self.closed = True

    @property
    def last(self) -> HttpRequest:
        assert self.requests, "No request was sent."
        return self.requests[-1]

    def last_json(self) -> dict[str, Any]:
        body = self.last.body
        assert body is not None, "The last request had no body."
        decoded = json.loads(body)
        assert isinstance(decoded, dict)
        return decoded


class AsyncFakeTransport:
    def __init__(self, *answers: HttpResponse | BaseException) -> None:
        self.sync = FakeTransport(*answers)
        self.closed = False

    async def send(self, request: HttpRequest) -> HttpResponse:
        return self.sync.send(request)

    async def aclose(self) -> None:
        self.closed = True


SENT = {
    "success": True,
    "messageId": "6ae7957285431f6150ce52fc",
    "queued": False,
    "data": {
        "messageId": "6ae7957285431f6150ce52fc",
        "branded": False,
        "queued": False,
        "remaining": {"clean": 95, "total": "unlimited"},
    },
}

STATUS = {
    "success": True,
    "data": {
        "id": "6ae7957285431f6150ce52fc",
        "phoneNumber": "+14155550123",
        "status": "sent",
        "deliveryStatus": "sent",
        "sentAt": "2026-11-01T19:39:30.329Z",
        "errorMessage": None,
    },
}

RECEIVED = {
    "success": True,
    "data": {
        "messages": [
            {
                "id": "6ae660eed767f99747f866c9",
                "sender": "+40799000222",
                "message": "Is the shop open on Sunday?",
                "isEncrypted": False,
                "receivedAt": "2026-10-31T21:43:10.489Z",
                "simSlot": 1,
                "deviceId": "6ae660e96609f21a1184a210",
                "deviceName": "Store phone",
            },
            {
                "id": "6ae660e96609f21a1184a217",
                "sender": "+40712345678",
                "message": None,
                "isEncrypted": True,
                "encryptedPayload": "v1:abc",
                "receivedAt": "2026-10-31T21:34:05.638Z",
                "deviceId": "6ae660e96609f21a1184a210",
                "deviceName": "Store phone",
            },
        ],
        "total": 7,
        "page": 1,
        "limit": 2,
        "hasMore": True,
    },
}

DEVICES = {
    "success": True,
    "data": {
        "devices": [
            {
                "id": "6ae660e96609f21a1184a210",
                "name": "Store phone",
                "isOnline": True,
                "lastSeenAt": "2026-11-01T20:51:09.526Z",
            },
            {
                "id": "6ae660e96609f21a1184a211",
                "name": "Old Samsung",
                "isOnline": False,
                "lastSeenAt": "2026-10-30T19:43:05.638Z",
            },
        ],
        "online": 1,
        "total": 2,
    },
}

USAGE = {
    "success": True,
    "data": {
        "plan": "pro",
        "accountType": "standard",
        "usage": {"smsClean": 108, "smsBranded": 0, "twoFaClean": 45},
        "limits": {"smsClean": "unlimited", "twoFaClean": 100},
        "remaining": {"smsClean": "unlimited", "twoFaClean": 55},
    },
}
