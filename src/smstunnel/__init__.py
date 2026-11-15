"""SMSTunnel for Python: send texts from your own apps through your own Android phone, from your own number.

.. code-block:: python

    import os
    import smstunnel

    client = smstunnel.Client(os.environ["SMSTUNNEL_API_KEY"])
    result = client.send_sms("+14155550123", "Your order #1042 has shipped.")
"""

from ._async_client import AsyncClient
from ._client import Client
from ._core import DEFAULT_BASE_URL
from ._version import __version__
from .errors import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    SmsTunnelError,
    TransportError,
)
from .models import (
    Device,
    DeviceList,
    MessageStatus,
    ReceivedMessage,
    ReceivedMessages,
    ScheduledText,
    ScheduledTexts,
    SendResult,
    Usage,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "ApiError",
    "AsyncClient",
    "AuthenticationError",
    "Client",
    "Device",
    "DeviceList",
    "MessageStatus",
    "PermissionDeniedError",
    "RateLimitError",
    "ReceivedMessage",
    "ReceivedMessages",
    "ScheduledText",
    "ScheduledTexts",
    "SendResult",
    "SmsTunnelError",
    "TransportError",
    "Usage",
    "__version__",
]
