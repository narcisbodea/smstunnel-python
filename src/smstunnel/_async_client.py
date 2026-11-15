from __future__ import annotations

import importlib.util
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, overload

from ._core import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    Core,
    received_query,
    scheduled_answer,
    scheduled_path,
    scheduled_query,
    send_at_value,
    send_body,
    status_path,
)
from .models import DeviceList, MessageStatus, ReceivedMessages, ScheduledText, ScheduledTexts, SendResult, Usage
from .transport import AsyncHttpxTransport, AsyncTransport, HttpRequest, ThreadedAsyncTransport


class AsyncClient:
    """The async version of :class:`~smstunnel.Client`, with the same methods and errors.

    .. code-block:: python

        async with smstunnel.AsyncClient(os.environ["SMSTUNNEL_API_KEY"]) as client:
            result = await client.send_sms("+14155550123", "Your table is ready.")

    It sends through httpx when httpx is installed (``pip install "smstunnel[httpx]"``), and otherwise runs
    the standard-library transport in a worker thread. Close it with ``await client.aclose()`` or use
    ``async with``. Like the sync client, it never retries on its own: give a send an ``idempotency_key`` to
    retry it safely.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: AsyncTransport | None = None,
    ) -> None:
        self._core = Core(api_key, base_url, timeout)
        self._transport: AsyncTransport = transport if transport is not None else _default_transport()
        self._owns_transport = transport is None

    @property
    def base_url(self) -> str:
        return self._core.base_url

    @overload
    async def send_sms(
        self,
        to: str,
        message: str,
        *,
        device_id: str | None = ...,
        default_country_code: str | None = ...,
        idempotency_key: str | None = ...,
        send_at: None = ...,
    ) -> SendResult: ...

    @overload
    async def send_sms(
        self,
        to: str,
        message: str,
        *,
        device_id: str | None = ...,
        default_country_code: str | None = ...,
        idempotency_key: str | None = ...,
        send_at: datetime | str,
    ) -> ScheduledText: ...

    async def send_sms(
        self,
        to: str,
        message: str,
        *,
        device_id: str | None = None,
        default_country_code: str | None = None,
        idempotency_key: str | None = None,
        send_at: datetime | str | None = None,
    ) -> SendResult | ScheduledText:
        """Sends one text to one number, from your phone: now, or at ``send_at`` (then it returns a
        :class:`~smstunnel.ScheduledText`). See :meth:`smstunnel.Client.send_sms`."""
        body = send_body(to, message, device_id, default_country_code)
        if send_at is not None:
            body["sendAt"] = send_at_value(send_at)
        request = self._core.post("/api/v1/sms/send", body, idempotency_key=idempotency_key)
        answer, replayed = await self._send(request)
        if send_at is None:
            return SendResult.from_dict(answer, replayed=replayed)
        return ScheduledText.from_dict(scheduled_answer(answer), replayed=replayed)

    async def send_verification_code(
        self,
        to: str,
        message: str,
        *,
        device_id: str | None = None,
        default_country_code: str | None = None,
        idempotency_key: str | None = None,
    ) -> SendResult:
        """Sends a sign-in or verification code. See :meth:`smstunnel.Client.send_verification_code`."""
        body = send_body(to, message, device_id, default_country_code)
        request = self._core.post("/api/v1/sms/send-2fa", body, idempotency_key=idempotency_key)
        answer, replayed = await self._send(request)
        return SendResult.from_dict(answer, replayed=replayed)

    async def list_scheduled(
        self,
        scope: Literal["waiting", "done"] = "waiting",
        *,
        page: int = 1,
        limit: int = 50,
    ) -> ScheduledTexts:
        """The texts this API key scheduled. See :meth:`smstunnel.Client.list_scheduled`."""
        request = self._core.get("/api/v1/sms/scheduled", scheduled_query(scope, page, limit))
        return ScheduledTexts.from_dict(Core.data(await self._call(request)))

    async def get_scheduled(self, scheduled_id: str) -> ScheduledText:
        """One text this API key scheduled. See :meth:`smstunnel.Client.get_scheduled`."""
        request = self._core.get(scheduled_path(scheduled_id))
        return ScheduledText.from_dict(Core.data(await self._call(request)))

    async def cancel_scheduled(self, scheduled_id: str) -> ScheduledText:
        """Cancels a text this API key scheduled. See :meth:`smstunnel.Client.cancel_scheduled`."""
        request = self._core.delete(scheduled_path(scheduled_id))
        return ScheduledText.from_dict(Core.data(await self._call(request)))

    async def get_message_status(self, message_id: str) -> MessageStatus:
        """Where a text is now. See :meth:`smstunnel.Client.get_message_status`."""
        request = self._core.get(status_path(message_id))
        return MessageStatus.from_dict(Core.data(await self._call(request)))

    async def get_received_messages(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        since: datetime | str | None = None,
        phone: str | None = None,
        device_id: str | None = None,
    ) -> ReceivedMessages:
        """Texts your phone received, newest first. See :meth:`smstunnel.Client.get_received_messages`."""
        query = received_query(limit=limit, page=page, since=since, phone=phone, device_id=device_id)
        request = self._core.get("/api/v1/sms/received", query)
        return ReceivedMessages.from_dict(Core.data(await self._call(request)))

    async def get_devices(self) -> DeviceList:
        """The phones linked to the key's account, and which are online."""
        return DeviceList.from_dict(Core.data(await self._call(self._core.get("/api/v1/devices"))))

    async def get_usage(self) -> Usage:
        """Today's usage, the plan's limits and what is left."""
        return Usage.from_dict(Core.data(await self._call(self._core.get("/api/v1/account/usage"))))

    async def aclose(self) -> None:
        """Closes the transport the client made itself (a transport you passed in stays open)."""
        close = getattr(self._transport, "aclose", None)
        if self._owns_transport and callable(close):
            await close()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"<smstunnel.AsyncClient {self._core!r}>"

    async def _call(self, request: HttpRequest) -> dict[str, Any]:
        return Core.handle(await self._transport.send(request))

    async def _send(self, request: HttpRequest) -> tuple[dict[str, Any], bool]:
        """The answer to a send, and whether SMSTunnel replayed it for its ``Idempotency-Key``."""
        response = await self._transport.send(request)
        return Core.handle(response), Core.replayed(response)


def _default_transport() -> AsyncTransport:
    if importlib.util.find_spec("httpx") is not None:
        return AsyncHttpxTransport()
    return ThreadedAsyncTransport()
