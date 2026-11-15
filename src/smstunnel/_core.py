"""What the sync and async clients share: building requests and reading answers."""

from __future__ import annotations

import json
import platform
import re
import urllib.parse
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ._version import __version__
from .errors import ApiError, error_from_response
from .transport import HttpRequest, HttpResponse

DEFAULT_BASE_URL = "https://smstunnel.io"
DEFAULT_TIMEOUT = 30.0

_BASE_URL = re.compile(r"https?://[^/\s?#]+(/[^\s?#]*)?", re.IGNORECASE)
_USER_AGENT = f"smstunnel-python/{__version__} Python/{platform.python_version()}"
# What SMSTunnel accepts as an Idempotency-Key (printable ASCII, at most 255), without spaces at the ends: the server
# trims them, and a key of spaces only would silently mean no key at all.
_IDEMPOTENCY_KEY = re.compile(r"[\x21-\x7e](?:[\x20-\x7e]{0,253}[\x21-\x7e])?")
# What SMSTunnel accepts as sendAt: a full ISO 8601 date and time with its offset, at most milliseconds, ASCII digits.
_SEND_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:\d{2})", re.ASCII)
SCHEDULED_SCOPES = ("waiting", "done")


class Core:
    """Holds the key and the address, builds each request and turns each answer into data or an exception."""

    __slots__ = ("api_key", "base_url", "timeout")

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError(
                "An SMSTunnel API key is required. Create one in your SMSTunnel dashboard (Apps → Add an app)."
            )
        url = base_url.strip().rstrip("/") if isinstance(base_url, str) else ""
        if not _BASE_URL.fullmatch(url):
            raise ValueError(f'The SMSTunnel base URL must look like https://smstunnel.io (got "{base_url}").')
        if not timeout > 0:
            raise ValueError("The timeout must be a number of seconds above 0.")

        self.api_key = api_key.strip()
        self.base_url = url
        self.timeout = float(timeout)

    def get(self, path: str, query: Mapping[str, str] | None = None) -> HttpRequest:
        if query:
            path += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
        return HttpRequest("GET", self.base_url + path, self._headers(json_body=False), None, self.timeout)

    def post(self, path: str, body: Mapping[str, str], *, idempotency_key: str | None = None) -> HttpRequest:
        headers = self._headers(json_body=True)
        # Checked here, before a request exists: a bad key raises and nothing is sent.
        headers.update(idempotency_header(idempotency_key))
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return HttpRequest("POST", self.base_url + path, headers, payload, self.timeout)

    def delete(self, path: str) -> HttpRequest:
        return HttpRequest("DELETE", self.base_url + path, self._headers(json_body=False), None, self.timeout)

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def handle(response: HttpResponse) -> dict[str, Any]:
        """The decoded answer, or the matching :class:`~smstunnel.errors.ApiError`."""
        body = _decode(response.text)
        status = response.status_code

        if 300 <= status < 400:
            location = response.header("location")
            message = (
                f"SMSTunnel answered with a redirect to {location}. Set the base URL to that address."
                if location
                else f"SMSTunnel answered with a redirect ({status})."
            )
            raise ApiError(message, status, body=body, headers=response.headers)

        if not 200 <= status < 300:
            raise error_from_response(status, body, response.headers)

        if not isinstance(body, dict):
            raise ApiError(
                "SMSTunnel answered with something that is not JSON.", status, body=body, headers=response.headers
            )
        if body.get("success") is False:
            raise error_from_response(status, body, response.headers)
        return body

    @staticmethod
    def replayed(response: HttpResponse) -> bool:
        """``True`` when SMSTunnel answered a repeat of an ``Idempotency-Key`` with the first answer: no new text."""
        return (response.header("idempotent-replayed") or "").strip().lower() == "true"

    @staticmethod
    def data(body: Mapping[str, Any]) -> dict[str, Any]:
        value = body.get("data")
        return dict(value) if isinstance(value, Mapping) else {}

    def __repr__(self) -> str:
        return f"base_url={self.base_url!r}"


def send_body(to: str, message: str, device_id: str | None, default_country_code: str | None) -> dict[str, str]:
    if not isinstance(to, str) or not to.strip():
        raise ValueError("The recipient number is empty.")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("The text is empty.")

    body = {"to": to.strip(), "message": message}
    if device_id is not None and device_id.strip():
        body["deviceId"] = device_id.strip()
    if default_country_code is not None and default_country_code.strip():
        body["defaultCountryCode"] = default_country_code.strip()
    return body


def idempotency_header(key: object) -> dict[str, str]:
    """The ``Idempotency-Key`` header for ``key``, none when it is ``None``.

    :raises TypeError: when the key is not a ``str``.
    :raises ValueError: when it is not 1 to 255 printable ASCII characters without spaces at the ends.
    """
    if key is None:
        return {}
    if not isinstance(key, str):
        raise TypeError(f"idempotency_key must be a str, such as str(uuid.uuid4()), not {type(key).__name__}.")
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise ValueError(
            "idempotency_key must be 1 to 255 printable ASCII characters, without spaces at the ends "
            "(a UUID is a good choice)."
        )
    return {"Idempotency-Key": key}


def send_at_value(send_at: object) -> str:
    """``send_at`` as SMSTunnel takes it: a timezone-aware ``datetime`` as its ``isoformat()`` with its own offset, to
    the second (SMSTunnel takes at most milliseconds); a string as it is, once trimmed.

    :raises ValueError: for a naive ``datetime``, or a string that is not an ISO 8601 date and time with an offset.
    :raises TypeError: for anything else.
    """
    if isinstance(send_at, datetime):
        if send_at.utcoffset() is None:
            raise ValueError(
                "send_at must be timezone-aware: a naive datetime has no time zone, so the moment it means is not "
                "known (09:00 where?). Give it one, such as "
                'datetime(2026, 11, 16, 9, 0, tzinfo=ZoneInfo("Europe/Bucharest")) or '
                "datetime.now(timezone.utc) + timedelta(hours=1)."
            )
        return send_at.isoformat(timespec="seconds")
    if isinstance(send_at, str):
        text = send_at.strip()
        if not _SEND_AT.fullmatch(text):
            raise ValueError(
                "send_at must be an ISO 8601 date and time with its offset, such as 2026-11-16T09:00:00Z or "
                f"2026-11-16T11:00:00+02:00, or a timezone-aware datetime (got {send_at!r}). Without an offset, the "
                "moment to send at is not known."
            )
        return text
    raise TypeError(f"send_at must be a timezone-aware datetime or an ISO 8601 string, not {type(send_at).__name__}.")


def scheduled_answer(body: Mapping[str, Any]) -> dict[str, Any]:
    """The scheduled text of a send with ``sendAt``: ``data``, with the answer's own ``scheduledId`` and ``sendAt``
    when ``data`` lacks them."""
    data = Core.data(body)
    data.setdefault("id", body.get("scheduledId"))
    data.setdefault("sendAt", body.get("sendAt"))
    return data


def scheduled_query(scope: str, page: int, limit: int) -> dict[str, str]:
    if scope not in SCHEDULED_SCOPES:
        raise ValueError(f'Unknown scope {scope!r}. Use "waiting" or "done".')
    return {"scope": scope, "page": str(int(page)), "limit": str(int(limit))}


def scheduled_path(scheduled_id: str) -> str:
    if not isinstance(scheduled_id, str) or not scheduled_id.strip():
        raise ValueError("The scheduled text id is empty.")
    return "/api/v1/sms/scheduled/" + urllib.parse.quote(scheduled_id.strip(), safe="")


def status_path(message_id: str) -> str:
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("The message id is empty.")
    return "/api/v1/sms/status/" + urllib.parse.quote(message_id.strip(), safe="")


def received_query(
    *,
    limit: int | None,
    page: int | None,
    since: datetime | str | None,
    phone: str | None,
    device_id: str | None,
) -> dict[str, str]:
    query: dict[str, str] = {}
    if limit is not None:
        query["limit"] = str(int(limit))
    if page is not None:
        query["page"] = str(int(page))
    if isinstance(since, datetime):
        # A datetime without a timezone is taken as local time, like datetime.astimezone() does.
        query["since"] = since.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    elif since is not None and since.strip():
        query["since"] = since.strip()
    if phone is not None and phone.strip():
        query["phone"] = phone.strip()
    if device_id is not None and device_id.strip():
        query["deviceId"] = device_id.strip()
    return query


def _decode(text: str) -> Any:
    """The JSON answer, the raw text when it is not JSON, or None when it is empty."""
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text
