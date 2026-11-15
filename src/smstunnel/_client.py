from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any

from ._core import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, Core, received_query, send_body, status_path
from .models import DeviceList, MessageStatus, ReceivedMessages, SendResult, Usage
from .transport import HttpRequest, Transport, UrllibTransport


class Client:
    """Sends texts from your own apps through your own Android phone, from your own number.

    .. code-block:: python

        import os
        import smstunnel

        client = smstunnel.Client(os.environ["SMSTUNNEL_API_KEY"])
        result = client.send_sms("+14155550123", "Your order has shipped.")
        print(result.message_id)

    Every call raises :class:`~smstunnel.ApiError` when SMSTunnel answers with an error (with the subclasses
    :class:`~smstunnel.AuthenticationError` for 401, :class:`~smstunnel.PermissionDeniedError` for 403 and
    :class:`~smstunnel.RateLimitError` for 429) and :class:`~smstunnel.TransportError` when SMSTunnel cannot be
    reached. A client is safe to share between threads.

    The client never retries on its own. To retry a send safely, give it an ``idempotency_key`` (see
    :meth:`send_sms`).

    :param api_key: an API key from your SMSTunnel dashboard (``sk_live_...``). Keep it in an environment
        variable, never in your code.
    :param base_url: change it only for a self-hosted SMSTunnel or a test server.
    :param timeout: seconds for each request.
    :param transport: defaults to :class:`~smstunnel.transport.UrllibTransport`; pass a
        :class:`~smstunnel.transport.HttpxTransport` to use httpx.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport | None = None,
    ) -> None:
        self._core = Core(api_key, base_url, timeout)
        self._transport: Transport = transport if transport is not None else UrllibTransport()
        self._owns_transport = transport is None

    @property
    def base_url(self) -> str:
        return self._core.base_url

    def send_sms(
        self,
        to: str,
        message: str,
        *,
        device_id: str | None = None,
        default_country_code: str | None = None,
        idempotency_key: str | None = None,
    ) -> SendResult:
        """Sends one text to one number, from your phone.

        :param to: the number in international format, e.g. ``+14155550123``.
        :param message: up to 1600 characters (10 joined SMS).
        :param device_id: send through this phone (see :meth:`get_devices`); ``None`` lets SMSTunnel pick one
            of yours.
        :param default_country_code: e.g. ``"+40"``: completes numbers given without ``+`` or ``00``.
        :param idempotency_key: makes the call safe to retry. It goes out as the ``Idempotency-Key`` header. For
            24 hours, a repeat of the call with the same key gets the first answer back (the same message id)
            instead of a second text; the same key with a different text, number, phone or country code is
            refused (:class:`~smstunnel.ApiError` 422, ``error_code`` ``IDEMPOTENCY_KEY_REUSED``). Use one new key
            per text and the same key on every retry of it: ``str(uuid.uuid4())``, or your own id such as
            ``"order-1042-shipped"``. 1 to 255 printable ASCII characters, no spaces at the ends.
        :raises ValueError: when the number or the text is empty, or the key is not 1 to 255 printable ASCII
            characters without spaces at the ends.
        :raises TypeError: when the key is not a ``str``.
        """
        body = send_body(to, message, device_id, default_country_code)
        request = self._core.post("/api/v1/sms/send", body, idempotency_key=idempotency_key)
        return SendResult.from_dict(self._call(request))

    def send_verification_code(
        self,
        to: str,
        message: str,
        *,
        device_id: str | None = None,
        default_country_code: str | None = None,
        idempotency_key: str | None = None,
    ) -> SendResult:
        """Sends a sign-in or verification code.

        The text must contain the code, 4 to 10 digits standing on their own (``Your code is 482913``, or
        ``123-456``); SMSTunnel refuses it otherwise (400). Up to 320 characters. It counts against the
        separate verification-code quota.

        :param idempotency_key: as in :meth:`send_sms`: a retry with the same key can't send the code twice.
        :raises ValueError: when the number or the text is empty, or the key is not a valid key.
        :raises TypeError: when the key is not a ``str``.
        """
        body = send_body(to, message, device_id, default_country_code)
        request = self._core.post("/api/v1/sms/send-2fa", body, idempotency_key=idempotency_key)
        return SendResult.from_dict(self._call(request))

    def get_message_status(self, message_id: str) -> MessageStatus:
        """Where a text is now. Needs a key with the ``sms:read`` permission.

        :raises ApiError: 404 when the key's account has no such text.
        :raises ValueError: when the id is empty.
        """
        request = self._core.get(status_path(message_id))
        return MessageStatus.from_dict(Core.data(self._call(request)))

    def get_received_messages(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        since: datetime | str | None = None,
        phone: str | None = None,
        device_id: str | None = None,
    ) -> ReceivedMessages:
        """Texts your phone received, newest first.

        Needs a key you allowed to read received texts (``sms:inbox``, a separate opt-in per key); other keys
        get a :class:`~smstunnel.PermissionDeniedError`. The phone syncs only texts that arrive after you turn
        syncing on in the app.

        :param limit: texts per page (default 50, at most 100).
        :param page: from 1.
        :param since: only texts newer than this (a ``datetime`` without a timezone is local time).
        :param phone: only texts from this sender.
        :param device_id: only texts this phone received.
        """
        query = received_query(limit=limit, page=page, since=since, phone=phone, device_id=device_id)
        request = self._core.get("/api/v1/sms/received", query)
        return ReceivedMessages.from_dict(Core.data(self._call(request)))

    def get_devices(self) -> DeviceList:
        """The phones linked to the key's account, and which are online."""
        return DeviceList.from_dict(Core.data(self._call(self._core.get("/api/v1/devices"))))

    def get_usage(self) -> Usage:
        """Today's usage, the plan's limits and what is left."""
        return Usage.from_dict(Core.data(self._call(self._core.get("/api/v1/account/usage"))))

    def close(self) -> None:
        """Closes the transport the client made itself (a transport you passed in stays open)."""
        close = getattr(self._transport, "close", None)
        if self._owns_transport and callable(close):
            close()

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<smstunnel.Client {self._core!r}>"

    def _call(self, request: HttpRequest) -> dict[str, Any]:
        return Core.handle(self._transport.send(request))
