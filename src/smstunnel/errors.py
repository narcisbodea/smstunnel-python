"""The exceptions raised when a call does not go through.

- :class:`SmsTunnelError`: the base of all of them. Catch it to handle every failure.
- :class:`ApiError`: SMSTunnel answered with an error, with the subclasses :class:`AuthenticationError` (401),
  :class:`PermissionDeniedError` (403) and :class:`RateLimitError` (429).
- :class:`TransportError`: SMSTunnel could not be reached at all.
"""

from __future__ import annotations

import email.utils
import html
import re
import time
from collections.abc import Mapping
from typing import Any

__all__ = [
    "ApiError",
    "AuthenticationError",
    "PermissionDeniedError",
    "RateLimitError",
    "SmsTunnelError",
    "TransportError",
    "error_from_response",
]


class SmsTunnelError(Exception):
    """Every exception this library raises when a call does not go through. Catch it to handle them all."""


class TransportError(SmsTunnelError):
    """SMSTunnel could not be reached at all: DNS, TLS, a refused connection or a timeout.

    No answer came back, so there is no HTTP status.
    """


class ApiError(SmsTunnelError):
    """SMSTunnel answered, but with an error.

    - ``status_code``: the HTTP status.
    - ``error_code``: SMSTunnel's own code when it sends one, such as ``PERMISSION_REQUIRED``.
    - ``message`` (also ``str(error)``): what went wrong, in SMSTunnel's words.
    - ``body``: the decoded answer (a dict), the raw text when it was not JSON, or ``None`` when it was empty.

    Statuses worth handling, each with its own subclass:

    - 401 :class:`AuthenticationError`: the API key is wrong, was deleted, or its account is suspended.
    - 403 :class:`PermissionDeniedError`: the key lacks a permission, or the plan does not include the feature.
    - 429 :class:`RateLimitError`: too many requests; wait and try again.

    Others: 400 (SMSTunnel refused a field, for example a number it cannot read, or a verification text
    without a code), 404 (no such message or phone for this key), 5xx (a problem on SMSTunnel's side).
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        *,
        error_code: str | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.body = body
        #: The response headers, names in lower case.
        self.headers: dict[str, str] = {name.lower(): value for name, value in (headers or {}).items()}

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status_code={self.status_code!r}, "
            f"error_code={self.error_code!r}, message={self.message!r})"
        )


class AuthenticationError(ApiError):
    """401: the API key is wrong, was deleted or expired, or its account is suspended.

    Check the key in your SMSTunnel dashboard (**Apps**).
    """


class PermissionDeniedError(ApiError):
    """403: the key is valid but may not do this.

    Either it lacks a permission (``error_code`` is ``PERMISSION_REQUIRED`` and :attr:`permission` names it),
    such as ``sms:read`` for statuses or ``sms:inbox`` for received texts, or the plan does not include the
    feature.
    """

    @property
    def permission(self) -> str | None:
        """With ``PERMISSION_REQUIRED``: the permission the key lacks, such as ``sms:inbox``."""
        if isinstance(self.body, Mapping):
            value = self.body.get("permission")
            if isinstance(value, str):
                return value
        return None


class RateLimitError(ApiError):
    """429: too many requests in a short time (sending allows 30 requests a minute), or a daily limit is reached."""

    @property
    def retry_after(self) -> float | None:
        """Seconds to wait before trying again, when SMSTunnel says (the ``Retry-After`` header)."""
        value = self.headers.get("retry-after", "").strip()
        if not value:
            return None
        if _SECONDS.fullmatch(value):
            return float(value)
        try:
            moment = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, moment.timestamp() - time.time())


_FALLBACK = "SMSTunnel returned error: {status}"
_SECONDS = re.compile(r"\d+(\.\d+)?")
_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")


def error_from_response(status_code: int, body: Any, headers: Mapping[str, str] | None = None) -> ApiError:
    """Builds the exception that matches the status: one of the subclasses for 401, 403 and 429.

    ``body`` is the decoded JSON answer, the raw text when it was not JSON, or ``None`` when it was empty.
    """
    message = _message_of(body, status_code)
    error_code: str | None = None
    if isinstance(body, Mapping) and isinstance(body.get("code"), str):
        error_code = body["code"]

    kind: type[ApiError] = {
        401: AuthenticationError,
        403: PermissionDeniedError,
        429: RateLimitError,
    }.get(status_code, ApiError)
    return kind(message, status_code, error_code=error_code, body=body, headers=headers)


def _message_of(body: Any, status_code: int) -> str:
    if isinstance(body, str):
        # A proxy's error page, say: its words without the markup, at most 300 characters.
        text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", body))).strip()
        return text[:300] if text else _FALLBACK.format(status=status_code)

    if isinstance(body, Mapping):
        message = body.get("message")
        if isinstance(message, list):
            message = "; ".join(part for part in message if isinstance(part, str))
        if isinstance(message, str) and message:
            return message
        error = body.get("error")
        if isinstance(error, str) and error:
            return error

    return _FALLBACK.format(status=status_code)
