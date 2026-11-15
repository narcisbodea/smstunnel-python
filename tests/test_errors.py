from __future__ import annotations

import email.utils
import time

import pytest

from smstunnel import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    SmsTunnelError,
    TransportError,
)
from smstunnel.errors import error_from_response


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ApiError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, ApiError),
        (409, ApiError),
        (422, ApiError),
        (429, RateLimitError),
        (500, ApiError),
    ],
)
def test_each_status_gets_its_exception(status: int, kind: type[ApiError]) -> None:
    error = error_from_response(status, {"message": "words"})

    assert type(error) is kind
    assert error.status_code == status
    assert isinstance(error, SmsTunnelError)


def test_every_exception_is_an_smstunnel_error() -> None:
    assert issubclass(TransportError, SmsTunnelError)
    assert not issubclass(TransportError, ApiError)
    for kind in (AuthenticationError, PermissionDeniedError, RateLimitError):
        assert issubclass(kind, ApiError)


def test_joins_a_list_of_messages() -> None:
    body = {"message": ["to must be a phone number", "message should not be empty"], "statusCode": 400}

    assert str(error_from_response(400, body)) == "to must be a phone number; message should not be empty"


def test_falls_back_to_the_error_label_then_to_the_status() -> None:
    assert error_from_response(403, {"error": "Forbidden"}).message == "Forbidden"
    assert error_from_response(500, {}).message == "SMSTunnel returned error: 500"
    assert error_from_response(500, None).message == "SMSTunnel returned error: 500"
    assert error_from_response(500, "   ").message == "SMSTunnel returned error: 500"
    assert error_from_response(500, ["unexpected"]).message == "SMSTunnel returned error: 500"


def test_keeps_the_code_and_the_permission() -> None:
    error = error_from_response(403, {"message": "No", "code": "PERMISSION_REQUIRED", "permission": "sms:read"})

    assert isinstance(error, PermissionDeniedError)
    assert error.error_code == "PERMISSION_REQUIRED"
    assert error.permission == "sms:read"
    assert error_from_response(403, {"message": "No"}).error_code is None
    no_permission = error_from_response(403, "Forbidden")
    assert isinstance(no_permission, PermissionDeniedError)
    assert no_permission.permission is None


def test_shortens_a_long_page_to_its_first_300_characters() -> None:
    error = error_from_response(500, "<p>" + "x" * 1000 + "</p>")

    assert error.message == "x" * 300


def test_unescapes_html_entities() -> None:
    assert error_from_response(503, "<h1>Down &amp; out</h1>").message == "Down & out"


def test_headers_are_kept_in_lower_case() -> None:
    error = error_from_response(500, None, {"X-Request-Id": "abc"})

    assert error.headers == {"x-request-id": "abc"}


class TestRetryAfter:
    def test_reads_seconds(self) -> None:
        error = error_from_response(429, None, {"retry-after": "12"})

        assert isinstance(error, RateLimitError)
        assert error.retry_after == 12.0

    def test_reads_a_date(self) -> None:
        later = email.utils.formatdate(time.time() + 30, usegmt=True)
        error = error_from_response(429, None, {"Retry-After": later})

        assert isinstance(error, RateLimitError)
        assert error.retry_after is not None
        assert 25 <= error.retry_after <= 31

    @pytest.mark.parametrize("value", ["", "soon", "-5", "inf", "nan"])
    def test_is_none_when_it_cannot_tell(self, value: str) -> None:
        error = error_from_response(429, None, {"Retry-After": value})

        assert isinstance(error, RateLimitError)
        assert error.retry_after is None

    def test_is_none_without_the_header(self) -> None:
        error = error_from_response(429, None)

        assert isinstance(error, RateLimitError)
        assert error.retry_after is None

    def test_is_on_every_api_error_such_as_a_key_still_in_progress(self) -> None:
        error = error_from_response(409, {"code": "IDEMPOTENCY_KEY_IN_PROGRESS"}, {"Retry-After": "1"})

        assert type(error) is ApiError
        assert error.error_code == "IDEMPOTENCY_KEY_IN_PROGRESS"
        assert error.retry_after == 1.0
        assert error_from_response(500, None).retry_after is None


def test_repr_shows_status_code_and_message() -> None:
    error = error_from_response(401, {"message": "Invalid or expired API key"})

    assert repr(error) == (
        "AuthenticationError(status_code=401, error_code=None, message='Invalid or expired API key')"
    )
