"""What SMSTunnel answers, as typed, read-only objects.

Every object keeps the whole answer in ``raw``, for fields this version does not map yet.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Literal

from . import _fields as f

__all__ = [
    "Device",
    "DeviceList",
    "MessageStatus",
    "ReceivedMessage",
    "ReceivedMessages",
    "ScheduledText",
    "ScheduledTexts",
    "SendResult",
    "Usage",
]


@dataclass(frozen=True, slots=True)
class SendResult:
    """SMSTunnel accepted the text and handed it to your phone (or will, as soon as the phone is back online)."""

    #: Keep it to look up the status later with ``get_message_status()``.
    message_id: str
    #: ``True`` when the phone was offline: it sends the text as soon as it is back.
    queued: bool = False
    #: ``True`` when the text carries a short SMSTunnel signature (plans with a free quota).
    branded: bool = False
    #: What is left of today's quota, e.g. ``{"clean": 95, "total": "unlimited"}`` (``-1`` means no limit).
    remaining: Mapping[str, int | str] = field(default_factory=dict)
    #: The whole answer.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)
    #: ``True`` when SMSTunnel answered a repeat of the ``idempotency_key`` with the first answer (the
    #: ``Idempotent-Replayed`` header): this call sent no new text, the first one did.
    replayed: bool = False

    @classmethod
    def from_dict(cls, body: Mapping[str, Any], *, replayed: bool = False) -> SendResult:
        """From the answer of ``POST /api/v1/sms/send`` or ``/send-2fa`` (``replayed``: it came with
        ``Idempotent-Replayed: true``)."""
        data = f.obj(body, "data")
        return cls(
            message_id=f.opt_string(body, "messageId") or f.string(data, "messageId"),
            queued=f.boolean(body, "queued", f.boolean(data, "queued")),
            branded=f.boolean(data, "branded"),
            remaining=f.counters(data, "remaining"),
            raw=dict(body),
            replayed=replayed,
        )


@dataclass(frozen=True, slots=True)
class MessageStatus:
    """Where a text is now."""

    PENDING: ClassVar[str] = "pending"
    #: The phone sent it. The last good status today: the app gets no delivery reports, so a text that went out stays
    #: ``sent``.
    SENT: ClassVar[str] = "sent"
    #: Reserved for a delivery report. The app gets none today, so the texts you send don't reach this status.
    DELIVERED: ClassVar[str] = "delivered"
    FAILED: ClassVar[str] = "failed"
    #: Handed to the SIM, but the phone got no confirmation. Final: SMSTunnel never retries it.
    UNCONFIRMED: ClassVar[str] = "unconfirmed"

    id: str
    #: The number the text went to.
    phone_number: str
    #: ``pending``, ``sent``, ``delivered`` or ``failed`` (an unconfirmed text shows as ``failed``).
    status: str
    #: The real state, which can also be ``unconfirmed``.
    delivery_status: str | None = None
    #: ``True`` when the phone handed the text to the SIM but got no confirmation.
    unconfirmed: bool = False
    sent_at: datetime | None = None
    #: Reserved, like ``DELIVERED``: ``None`` for the texts you send today.
    delivered_at: datetime | None = None
    #: Why it failed, or ``None``.
    error_message: str | None = None
    #: The whole ``data`` object.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MessageStatus:
        """From the ``data`` object of ``GET /api/v1/sms/status/:id``."""
        return cls(
            id=f.string(data, "id"),
            phone_number=f.string(data, "phoneNumber"),
            status=f.string(data, "status", cls.PENDING),
            delivery_status=f.opt_string(data, "deliveryStatus"),
            unconfirmed=f.boolean(data, "unconfirmed"),
            sent_at=f.moment(data, "sentAt"),
            delivered_at=f.moment(data, "deliveredAt"),
            error_message=f.opt_string(data, "errorMessage"),
            raw=dict(data),
        )

    @property
    def is_final(self) -> bool:
        """``True`` once there is nothing more to wait for: sent, delivered, failed or unconfirmed.

        A text the phone sent stays ``sent``, because the app gets no delivery reports.
        """
        return (self.delivery_status or self.status) in (self.SENT, self.DELIVERED, self.FAILED, self.UNCONFIRMED)


@dataclass(frozen=True, slots=True)
class ScheduledText:
    """A text your app scheduled: ``send_sms(..., send_at=...)`` answers with one, and ``list_scheduled()``,
    ``get_scheduled()`` and ``cancel_scheduled()`` read them.

    At ``send_at`` SMSTunnel sends it as if your app called ``send_sms()`` then, with the same API key: the phone, the
    country code and the day's allowance are those of that moment. Once it went out, ``message_id`` is set: look up
    its status with ``get_message_status()``.
    """

    #: Waiting for its time (and, when the phone is offline then, for the phone, up to 3 days).
    SCHEDULED: ClassVar[str] = "scheduled"
    #: Handed to the phone at its time: ``message_id`` is set.
    SENT: ClassVar[str] = "sent"
    #: Not sent: ``error`` says why.
    FAILED: ClassVar[str] = "failed"
    CANCELED: ClassVar[str] = "canceled"

    #: Keep it to read or cancel the text (the send's ``scheduledId``).
    id: str
    #: The number, as your app gave it.
    to: str
    message: str
    #: When it goes out (SMSTunnel answers in UTC).
    send_at: datetime | None
    #: ``scheduled``, ``sent``, ``failed`` or ``canceled``.
    status: str = "scheduled"
    #: The phone your app named, or ``None``: SMSTunnel picks one at its time.
    device_id: str | None = None
    #: Once it went out: the text's id, for ``get_message_status()``.
    message_id: str | None = None
    #: Why it failed: ``limit_reached``, ``key_not_active``, ``invalid_number``, ``phone_not_found``,
    #: ``phone_unlinked``, ``sealed_only_phone``, ``number_not_allowed``, ``account_not_active``, ``interrupted`` or
    #: ``failed``.
    error: str | None = None
    created_at: datetime | None = None
    #: The whole object.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)
    #: From ``send_sms(..., send_at=...)`` only: ``True`` when SMSTunnel answered a repeat of the ``idempotency_key``
    #: with the first answer, so this call scheduled no new text.
    replayed: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, replayed: bool = False) -> ScheduledText:
        """From one scheduled text: the ``data`` of ``GET /api/v1/sms/scheduled/:id``, or an item of the list."""
        return cls(
            id=f.string(data, "id"),
            to=f.string(data, "to"),
            message=f.string(data, "message"),
            send_at=f.moment(data, "sendAt"),
            status=f.string(data, "status", cls.SCHEDULED),
            device_id=f.opt_string(data, "deviceId"),
            message_id=f.opt_string(data, "messageId"),
            error=f.opt_string(data, "error"),
            created_at=f.moment(data, "createdAt"),
            raw=dict(data),
            replayed=replayed,
        )


@dataclass(frozen=True, slots=True)
class ScheduledTexts:
    """One page of the texts your API key scheduled. Iterate it, or read ``items``."""

    #: The texts still waiting, soonest first.
    WAITING: ClassVar[Literal["waiting"]] = "waiting"
    #: The texts that went out, failed or were canceled in the last 30 days, newest first.
    DONE: ClassVar[Literal["done"]] = "done"

    items: tuple[ScheduledText, ...]
    #: The key's texts in this scope, on every page.
    total: int
    page: int
    limit: int
    #: The texts waiting in the whole account, from every key and the dashboard.
    waiting: int
    #: How many texts the plan lets wait at once.
    max: int
    #: The whole ``data`` object.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScheduledTexts:
        """From the ``data`` object of ``GET /api/v1/sms/scheduled``."""
        return cls(
            items=tuple(ScheduledText.from_dict(item) for item in f.objects(data, "items")),
            total=f.integer(data, "total"),
            page=f.integer(data, "page", 1),
            limit=f.integer(data, "limit"),
            waiting=f.integer(data, "waiting"),
            max=f.integer(data, "max"),
            raw=dict(data),
        )

    def __iter__(self) -> Iterator[ScheduledText]:
        return iter(self.items)

    def __len__(self) -> int:
        """The texts on this page (``total`` counts all of them)."""
        return len(self.items)


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    """A text your phone received and synced to your account."""

    id: str
    #: The number that sent it.
    sender: str
    #: The words; ``None`` when the phone synced it encrypted (see ``encrypted_payload``).
    message: str | None
    #: When the phone received it.
    received_at: datetime | None
    is_encrypted: bool = False
    encrypted_payload: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    sim_slot: int | None = None
    subscription_id: int | None = None
    #: The whole object.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReceivedMessage:
        """From one item of ``data.messages`` of ``GET /api/v1/sms/received``."""
        return cls(
            id=f.string(data, "id"),
            sender=f.string(data, "sender"),
            message=f.opt_string(data, "message"),
            received_at=f.moment(data, "receivedAt"),
            is_encrypted=f.boolean(data, "isEncrypted"),
            encrypted_payload=f.opt_string(data, "encryptedPayload"),
            device_id=f.opt_string(data, "deviceId"),
            device_name=f.opt_string(data, "deviceName"),
            sim_slot=f.opt_int(data, "simSlot"),
            subscription_id=f.opt_int(data, "subscriptionId"),
            raw=dict(data),
        )


@dataclass(frozen=True, slots=True)
class ReceivedMessages:
    """One page of received texts, newest first. Iterate it, or read ``messages``."""

    messages: tuple[ReceivedMessage, ...]
    #: All texts that match the filters, on every page.
    total: int
    page: int
    limit: int
    #: ``True`` when there is a next page.
    has_more: bool
    #: The whole ``data`` object.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReceivedMessages:
        """From the ``data`` object of ``GET /api/v1/sms/received``."""
        return cls(
            messages=tuple(ReceivedMessage.from_dict(item) for item in f.objects(data, "messages")),
            total=f.integer(data, "total"),
            page=f.integer(data, "page", 1),
            limit=f.integer(data, "limit"),
            has_more=f.boolean(data, "hasMore"),
            raw=dict(data),
        )

    def __iter__(self) -> Iterator[ReceivedMessage]:
        return iter(self.messages)

    def __len__(self) -> int:
        """The texts on this page (``total`` counts all of them)."""
        return len(self.messages)


@dataclass(frozen=True, slots=True)
class Device:
    """A phone linked to your SMSTunnel account."""

    #: Pass it as ``device_id`` to send through this phone.
    id: str
    name: str
    is_online: bool
    last_seen_at: datetime | None = None
    #: The whole object.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Device:
        """From one item of ``data.devices`` of ``GET /api/v1/devices``."""
        return cls(
            id=f.string(data, "id"),
            name=f.string(data, "name"),
            is_online=f.boolean(data, "isOnline"),
            last_seen_at=f.moment(data, "lastSeenAt"),
            raw=dict(data),
        )


@dataclass(frozen=True, slots=True)
class DeviceList:
    """The phones of the API key's account. Iterate it, or read ``devices``."""

    devices: tuple[Device, ...]
    online: int
    total: int
    #: The whole ``data`` object.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeviceList:
        """From the ``data`` object of ``GET /api/v1/devices``."""
        devices = tuple(Device.from_dict(item) for item in f.objects(data, "devices"))
        return cls(
            devices=devices,
            online=f.integer(data, "online", sum(1 for device in devices if device.is_online)),
            total=f.integer(data, "total", len(devices)),
            raw=dict(data),
        )

    def find(self, device_id: str) -> Device | None:
        """The phone with this id, or ``None``."""
        return next((device for device in self.devices if device.id == device_id), None)

    def __iter__(self) -> Iterator[Device]:
        return iter(self.devices)

    def __len__(self) -> int:
        return len(self.devices)


@dataclass(frozen=True, slots=True)
class Usage:
    """What the account used today, and what is left.

    A client of a company account shares the company's quota and sees it here.
    """

    #: e.g. ``"pro"``.
    plan: str
    account_type: str
    #: e.g. ``{"smsClean": 15, "smsBranded": 0, "twoFaClean": 4}``.
    usage: Mapping[str, int | str] = field(default_factory=dict)
    #: e.g. ``{"smsClean": "unlimited", "twoFaClean": 100}``.
    limits: Mapping[str, int | str] = field(default_factory=dict)
    #: Same keys as ``limits``.
    remaining: Mapping[str, int | str] = field(default_factory=dict)
    #: ``{"id": ..., "name": ...}`` of the company, for a client of a company account.
    enterprise: Mapping[str, str] | None = None
    #: The whole ``data`` object.
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Usage:
        """From the ``data`` object of ``GET /api/v1/account/usage``."""
        enterprise = f.obj(data, "enterprise")
        return cls(
            plan=f.string(data, "plan"),
            account_type=f.string(data, "accountType"),
            usage=f.counters(data, "usage"),
            limits=f.counters(data, "limits"),
            remaining=f.counters(data, "remaining"),
            enterprise=(
                {"id": f.string(enterprise, "id"), "name": f.string(enterprise, "name")} if enterprise else None
            ),
            raw=dict(data),
        )
