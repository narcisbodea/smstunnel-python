"""How a request reaches SMSTunnel.

The clients ship with:

- :class:`UrllibTransport`: the default, the standard library only.
- :class:`HttpxTransport` and :class:`AsyncHttpxTransport`: through `httpx <https://www.python-httpx.org>`_
  (``pip install "smstunnel[httpx]"``), to share your app's HTTP setup.
- :class:`ThreadedAsyncTransport`: runs a synchronous transport in a worker thread, for the async client.

Implement :class:`Transport` (or :class:`AsyncTransport`), one method, to plug in something else or to fake
SMSTunnel in your own tests. A transport hands back every answer, whatever its status, and raises
:class:`~smstunnel.errors.TransportError` only when no answer came back.
"""

from __future__ import annotations

import asyncio
import http.client
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .errors import TransportError

if TYPE_CHECKING:
    import httpx

__all__ = [
    "AsyncHttpxTransport",
    "AsyncTransport",
    "HttpRequest",
    "HttpResponse",
    "HttpxTransport",
    "ThreadedAsyncTransport",
    "Transport",
    "UrllibTransport",
]


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """One request to SMSTunnel, as the client hands it to a transport."""

    #: ``GET`` or ``POST``.
    method: str
    url: str
    headers: Mapping[str, str]
    #: The JSON body, or ``None``.
    body: bytes | None
    #: Seconds for the whole request.
    timeout: float


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """The raw answer, as a transport hands it back to the client."""

    status_code: int
    text: str = ""
    #: Header names in lower case.
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


@runtime_checkable
class Transport(Protocol):
    """Sends one request and hands back the answer, whatever its status."""

    def send(self, request: HttpRequest) -> HttpResponse:
        """Raise :class:`~smstunnel.errors.TransportError` when SMSTunnel could not be reached."""
        ...


@runtime_checkable
class AsyncTransport(Protocol):
    """The async version of :class:`Transport`."""

    async def send(self, request: HttpRequest) -> HttpResponse:
        """Raise :class:`~smstunnel.errors.TransportError` when SMSTunnel could not be reached."""
        ...


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect comes back as an answer: the API key is never sent to another address."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class UrllibTransport:
    """The default transport: ``urllib.request``, nothing else.

    It never follows redirects, so the API key is never sent to another address. It uses the proxy from
    the ``HTTPS_PROXY`` / ``HTTP_PROXY`` environment variables, like the rest of the standard library.

    :param ssl_context: to trust your own certificate authority, for example; the default verifies
        certificates against the system's store.
    """

    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        context = ssl_context if ssl_context is not None else ssl.create_default_context()
        self._opener = urllib.request.build_opener(_NoRedirects(), urllib.request.HTTPSHandler(context=context))

    def send(self, request: HttpRequest) -> HttpResponse:
        prepared = urllib.request.Request(  # noqa: S310 - the client only builds http(s) URLs
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with self._opener.open(prepared, timeout=request.timeout) as answer:
                return _urllib_response(answer.status, answer.headers, answer.read())
        except urllib.error.HTTPError as error:
            # 3xx, 4xx and 5xx: an answer, not a failure to reach SMSTunnel.
            return _urllib_response(error.code, error.headers, _read_quietly(error))
        except urllib.error.URLError as error:
            raise TransportError(_unreachable(request, error.reason)) from error
        except (OSError, http.client.HTTPException) as error:
            raise TransportError(_unreachable(request, error)) from error


def _read_quietly(error: urllib.error.HTTPError) -> bytes:
    if getattr(error, "fp", None) is None:
        return b""
    try:
        return error.read()
    except (OSError, http.client.HTTPException):
        return b""
    finally:
        error.close()


def _urllib_response(status: int, headers: Message, body: bytes) -> HttpResponse:
    charset = headers.get_content_charset() or "utf-8"
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    return HttpResponse(status, text, {name.lower(): value for name, value in headers.items()})


def _unreachable(request: HttpRequest, reason: object, *, timed_out: bool = False) -> str:
    if timed_out or isinstance(reason, TimeoutError):
        return f"Could not reach SMSTunnel at {_origin(request.url)}: no answer within {request.timeout:g} s."
    return f"Could not reach SMSTunnel at {_origin(request.url)}: {reason}"


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else url


def _import_httpx() -> Any:
    try:
        import httpx
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise ImportError('This transport needs httpx: pip install "smstunnel[httpx]"') from error
    return httpx


class HttpxTransport:
    """Sends through an ``httpx.Client``, so SMSTunnel calls share your app's proxy, retry and logging setup.

    :param client: your own ``httpx.Client``; you close it. Without one, the transport makes its own and
        closes it with the SMSTunnel client.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._httpx = _import_httpx()
        self._client: httpx.Client = client if client is not None else self._httpx.Client()
        self._owns_client = client is None

    def send(self, request: HttpRequest) -> HttpResponse:
        try:
            answer = self._client.request(
                request.method,
                request.url,
                headers=dict(request.headers),
                content=request.body,
                timeout=request.timeout,
                follow_redirects=False,
            )
        except self._httpx.TimeoutException as error:
            raise TransportError(_unreachable(request, error, timed_out=True)) from error
        except self._httpx.TransportError as error:
            raise TransportError(_unreachable(request, error)) from error
        return HttpResponse(
            answer.status_code, answer.text, {name.lower(): value for name, value in answer.headers.items()}
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class AsyncHttpxTransport:
    """Sends through an ``httpx.AsyncClient``. The async client uses it by default when httpx is installed.

    :param client: your own ``httpx.AsyncClient``; you close it. Without one, the transport makes its own
        and closes it with the SMSTunnel client.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._httpx = _import_httpx()
        self._client: httpx.AsyncClient = client if client is not None else self._httpx.AsyncClient()
        self._owns_client = client is None

    async def send(self, request: HttpRequest) -> HttpResponse:
        try:
            answer = await self._client.request(
                request.method,
                request.url,
                headers=dict(request.headers),
                content=request.body,
                timeout=request.timeout,
                follow_redirects=False,
            )
        except self._httpx.TimeoutException as error:
            raise TransportError(_unreachable(request, error, timed_out=True)) from error
        except self._httpx.TransportError as error:
            raise TransportError(_unreachable(request, error)) from error
        return HttpResponse(
            answer.status_code, answer.text, {name.lower(): value for name, value in answer.headers.items()}
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ThreadedAsyncTransport:
    """Runs a synchronous :class:`Transport` in a worker thread, so it can serve the async client.

    The async client uses it, around :class:`UrllibTransport`, when httpx is not installed.
    """

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport: Transport = transport if transport is not None else UrllibTransport()

    async def send(self, request: HttpRequest) -> HttpResponse:
        return await asyncio.to_thread(self._transport.send, request)
