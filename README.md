# SMSTunnel for Python

`smstunnel`: a small Python client for **[SMSTunnel](https://smstunnel.io)**. Send texts from your own Python apps through **your own Android phone**, from **your own number**.

Your app hands the text to your SMSTunnel account and your phone sends it from its own SIM, so recipients see a number they know and can reply to. The phone is linked to your account when you sign in to the SMSTunnel app (or scan the QR code from your dashboard), and you can cut an app off at any time by deleting its API key in your dashboard.

Your phone needs the SMSTunnel app from Google Play:

<a href="https://play.google.com/store/apps/details?id=io.smstunnel.app"><img src="https://smstunnel.io/google-play-badge.png" alt="Get it on Google Play" height="56"></a>

## Requirements

- Python 3.10 or newer
- No dependencies. [httpx](https://www.python-httpx.org) is optional (see [Your own HTTP client](#your-own-http-client)).

## Installation

```bash
pip install smstunnel
```

## Get an API key

1. Install the [SMSTunnel app from Google Play](https://play.google.com/store/apps/details?id=io.smstunnel.app) on your Android phone and sign in. Sign in at [smstunnel.io](https://smstunnel.io) on your computer with the same account: the phone appears under **Phones** (you can also link it with the QR code from **Phones → Add phone**).
2. In your [SMSTunnel dashboard](https://dashboard.smstunnel.io/apps), open **Apps** → **Add an app** and create its key. The key can send texts and look up whether they went out (`sms:send` and `sms:read`).
3. Reading received texts only works with a key you have explicitly allowed to (**Can also read received texts**, the `sms:inbox` permission, a separate opt-in per key). Syncing received texts is off by default in the app; when you turn it on, only texts that arrive from then on are synced, and the phone's existing messages are never read.

Keep the key out of your code: put it in an environment variable such as `SMSTUNNEL_API_KEY`.

## Usage

```python
import os
import smstunnel

client = smstunnel.Client(os.environ["SMSTUNNEL_API_KEY"])  # sk_live_...

result = client.send_sms("+14155550123", "Your order #1042 has shipped.")

print(result.message_id)  # keep it to look up the status later
```

### Send a text

```python
result = client.send_sms(
    "+14155550123",               # international format
    "See you at 10:30.",          # up to 1600 characters
    device_id=None,               # a phone id from get_devices(), or None to let SMSTunnel pick one of yours
    default_country_code=None,    # e.g. "+40": completes numbers given without + or 00
    idempotency_key=None,         # e.g. "order-1042-shipped": a retry with the same key can't send it twice
)

result.message_id  # str
result.queued      # True when the phone was offline: it sends the text as soon as it is back
result.branded     # True when the text carries a short SMSTunnel signature (plans with a free quota)
result.remaining   # today's quota left, e.g. {"clean": 95, "total": "unlimited"} (-1 means no limit)
```

One call sends one text to one number. To text several people, call `send_sms()` once for each.

### Send a sign-in or verification code

```python
client.send_verification_code("+14155550123", "Your sign-in code is 482913")
```

The text must contain the code, 4 to 10 digits standing on their own (for example `482913` or `123-456`); SMSTunnel refuses it otherwise. Codes count against a separate quota, and the text can be up to 320 characters.

### Look up a status

Needs a key with `sms:read`.

```python
from smstunnel import MessageStatus

status = client.get_message_status(result.message_id)

status.status           # "pending", "sent", "delivered" or "failed"
status.delivery_status  # the same, or "unconfirmed": handed to the SIM but the phone got no confirmation
status.sent_at          # a datetime (UTC), or None
status.error_message    # why it failed, or None
status.is_final         # True once it will not change any more

if status.status == MessageStatus.FAILED:
    ...
```

### Read received texts

Needs a key you allowed to read received texts (`sms:inbox`) and syncing turned on in the app.

```python
from datetime import datetime, timedelta, timezone

page = client.get_received_messages(
    limit=20,                                            # at most 100
    page=1,
    since=datetime.now(timezone.utc) - timedelta(days=1),  # or an ISO 8601 string
    phone="+14155550123",                                # only from this sender
    device_id=None,                                      # only this phone
)

for text in page:
    print(text.sender, text.message)  # text.received_at, text.device_name, ...

page.total     # all texts that match, on every page
page.has_more  # ask for the next page
```

### Your phones and your usage

```python
for phone in client.get_devices():
    print(phone.name, "online" if phone.is_online else "offline")  # phone.id, phone.last_seen_at

usage = client.get_usage()
usage.plan       # e.g. "pro"
usage.usage      # {"smsClean": 15, "smsBranded": 0, "twoFaClean": 4}
usage.remaining  # {"smsClean": "unlimited", "twoFaClean": 96}
```

Every response object is a read-only dataclass with a `raw` field, the whole answer, for fields this version does not map yet. The package ships type hints (`py.typed`).

### Async

`smstunnel.AsyncClient` has the same methods, as coroutines:

```python
import asyncio
import os

import smstunnel


async def main() -> None:
    async with smstunnel.AsyncClient(os.environ["SMSTUNNEL_API_KEY"]) as client:
        await client.send_sms("+14155550123", "Your table is ready.")


asyncio.run(main())
```

It sends through httpx when httpx is installed (`pip install "smstunnel[httpx]"`); otherwise it runs the standard-library transport in a worker thread.

## Errors

Every call raises a `smstunnel.SmsTunnelError` when it does not go through:

| Exception | When |
| --- | --- |
| `AuthenticationError` (401) | The API key is wrong, was deleted, or its account is suspended. |
| `PermissionDeniedError` (403) | The key lacks a permission (`error_code` is `PERMISSION_REQUIRED`, `permission` names it, e.g. `sms:inbox`), or the plan does not include the feature. |
| `RateLimitError` (429) | Too many requests. `retry_after` gives the seconds to wait, when SMSTunnel says. |
| `ApiError` | Any other error answer: 400 (a number it cannot read, a verification text without a code), 404 (no such message), 409 and 422 for an `idempotency_key` (see [Retrying safely](#retrying-safely)), 5xx. The three above extend it. |
| `TransportError` | SMSTunnel could not be reached (DNS, TLS, timeout). |

```python
import logging
import time

from smstunnel import ApiError, RateLimitError, TransportError

try:
    client.send_sms("+14155550123", "Your table is ready.")
except RateLimitError as error:
    time.sleep(error.retry_after or 30)  # then try again
except ApiError as error:
    logging.warning("SMSTunnel %s %s: %s", error.status_code, error.error_code or "-", error.message)
except TransportError:
    ...  # network problem: try again later
```

`message` (also `str(error)`) is SMSTunnel's own explanation, `status_code` the HTTP status, `body` the decoded answer, and `retry_after` the seconds to wait when SMSTunnel says. An empty number or text raises `ValueError` before anything is sent.

## Retrying safely

The client never retries on its own. To retry a text without risking a second copy, give the send an `idempotency_key`: one new key per text, and the same key on every retry of that text. It goes out as the `Idempotency-Key` header, and for 24 hours SMSTunnel answers a repeat with its first answer (the same `message_id`) instead of sending the text again.

```python
import time
import uuid

from smstunnel import ApiError, TransportError

key = str(uuid.uuid4())  # or your own id for this text, e.g. "order-1042-shipped"

for attempt in range(3):
    try:
        result = client.send_sms("+14155550123", "Your order #1042 has shipped.", idempotency_key=key)
        break
    except TransportError:
        if attempt == 2:
            raise
        time.sleep(2)  # no answer came, so the text may or may not have gone out: try again with the same key
    except ApiError as error:
        if error.retry_after is None or attempt == 2:
            raise  # a refusal: the same call would be refused again
        time.sleep(error.retry_after)  # 429 too many requests, or 409 while the first try is still running
```

- `send_verification_code()` and the `AsyncClient` methods take `idempotency_key` too.
- The same key with a different text, number, phone or country code gets an `ApiError` with `status_code` 422 and `error_code` `IDEMPOTENCY_KEY_REUSED`, and nothing goes out.
- A refusal (4xx) means nothing went out: fix the cause, then retry with the same key. A server error (5xx) is kept with the key, because the text may have gone out before it: a retry gets the same error back. Check your dashboard before you send that text again with a new key.
- A key is 1 to 255 printable ASCII characters, without spaces at the ends. Anything else raises `ValueError` (`TypeError` when it is not a `str`) before anything is sent.
- SMSTunnel remembers a key for 24 hours: a retry after that sends the text again.

## Django

Read the key from the environment in `settings.py`:

```python
# settings.py
import os

SMSTUNNEL_API_KEY = os.environ.get("SMSTUNNEL_API_KEY", "")
```

Make one client for the whole app, and send once the order is saved:

```python
# shop/sms.py
from functools import cache

import smstunnel
from django.conf import settings


@cache
def sms() -> smstunnel.Client:
    return smstunnel.Client(settings.SMSTUNNEL_API_KEY)
```

```python
# shop/views.py
import logging

from django.db import transaction
from django.shortcuts import get_object_or_404, redirect

from smstunnel import SmsTunnelError

from .models import Order
from .sms import sms

log = logging.getLogger(__name__)


def text_customer(order: Order) -> None:
    try:
        sms().send_sms(order.phone, f"Your order #{order.pk} has shipped.")
    except SmsTunnelError:
        log.exception("The shipping text for order %s did not go out", order.pk)


def mark_shipped(request, pk):
    order = get_object_or_404(Order, pk=pk)
    order.status = "shipped"
    order.save()
    transaction.on_commit(lambda: text_customer(order))
    return redirect("order-detail", pk=pk)
```

To keep requests fast, you can call `text_customer()` from a background task (Celery, RQ, Django's tasks) instead.

## Flask

```python
import os

from flask import Flask, abort, request
from smstunnel import Client, SmsTunnelError

app = Flask(__name__)
app.config["SMSTUNNEL_API_KEY"] = os.environ["SMSTUNNEL_API_KEY"]
sms = Client(app.config["SMSTUNNEL_API_KEY"])


@app.post("/appointments/<int:appointment_id>/remind")
def remind(appointment_id: int):
    phone = request.form.get("phone", "")
    if not phone.startswith("+"):
        abort(400, "Give the number in international format, e.g. +14155550123.")
    try:
        result = sms.send_sms(phone, "Reminder: your appointment is tomorrow at 10:30.")
    except SmsTunnelError as error:
        app.logger.warning("SMSTunnel: %s", error)
        return {"sent": False}, 502
    return {"sent": True, "messageId": result.message_id}
```

## Your own HTTP client

The default transport uses only the standard library (`urllib.request`), a 30-second timeout and never follows redirects, so the API key is never sent to another address. It uses the proxy from the `HTTPS_PROXY` environment variable. Change the timeout, or trust your own certificate authority:

```python
import ssl

from smstunnel import Client
from smstunnel.transport import UrllibTransport

client = Client(api_key, timeout=10, transport=UrllibTransport(ssl_context=ssl.create_default_context(cafile="ca.pem")))
```

Or send through httpx, so SMSTunnel calls share your app's HTTP setup:

```python
import httpx
from smstunnel import AsyncClient, Client
from smstunnel.transport import AsyncHttpxTransport, HttpxTransport

client = Client(api_key, transport=HttpxTransport(httpx.Client(proxy="http://proxy.internal:3128")))
async_client = AsyncClient(api_key, transport=AsyncHttpxTransport(httpx.AsyncClient()))
```

In your own tests, give the client an object with a `send(request) -> HttpResponse` method (see `smstunnel.transport.Transport`) to answer without a network, or mock `smstunnel.Client`.

`Client(api_key, "https://your-own-host")` points the client at a self-hosted SMSTunnel; the default is `https://smstunnel.io`.

## Notes and limits

- **Rate limit**: sending allows 30 requests a minute.
- **Length**: up to 1600 characters (10 joined SMS); verification codes up to 320.
- **Numbers**: use the international format (`+14155550123`). For local numbers, pass `default_country_code`: it always takes precedence (`+49`, `49` and `0049` all work).
- **Offline phone**: the text waits on your account (`queued` is `True`) and goes out when the phone is back online.

## Build it with an AI assistant

Paste this into Claude, ChatGPT, Cursor or Copilot, and replace the part in brackets. Never paste your real API key into a prompt.

```text
I want my Python app to send texts with SMSTunnel. SMSTunnel sends each text from my own Android phone, from my own number: the phone runs the SMSTunnel app and is linked to my SMSTunnel account.

Use the SMSTunnel package: `pip install smstunnel`, then `client = smstunnel.Client(os.environ["SMSTUNNEL_API_KEY"])`.
API facts:
- client.send_sms(to, message, device_id=None, default_country_code=None, idempotency_key=None) calls POST https://smstunnel.io/api/v1/sms/send with the header X-API-Key (and Idempotency-Key when given) and the JSON body {"to", "message", "deviceId"?, "defaultCountryCode"?}. It returns message_id and queued.
- One recipient per call. To text several people, call it once per number.
- Retries: the client never retries on its own. idempotency_key (one new key per text, such as str(uuid.uuid4()), the same on every retry of that text) makes a retry within 24 hours get the first answer back instead of sending a second text. The same key with a different text is refused: ApiError 422, error_code IDEMPOTENCY_KEY_REUSED.
- client.send_verification_code(...) (POST /api/v1/sms/send-2fa) is only for sign-in codes: the text must contain a code of 4 to 10 digits standing on its own (e.g. 482913 or 123-456), at most 320 characters; it uses a separate quota.
- Numbers in international format (+14155550123). default_country_code (e.g. "+40") completes numbers written without + or 00.
- Limits: 30 send requests a minute, 1600 characters a text.
- Errors: 401 AuthenticationError (bad key), 403 PermissionDeniedError (missing permission or plan; error_code such as PERMISSION_REQUIRED), 429 RateLimitError (retry_after seconds), other 4xx/5xx ApiError, TransportError when SMSTunnel can't be reached.
- If the phone is offline, the text waits on the account (queued is True) and goes out when the phone is back.

What I want: [describe what should send a text, to whom, and when]

Rules:
- Read the API key from an environment variable or the platform's secret store. Never put it in source code, in the repository or in client-side code.
- Validate phone numbers before sending, and handle every error above: log it clearly, don't retry 401/403, wait retry_after and retry 429. Retry a send only with the same idempotency_key, so the same text never goes out twice.
- Only send texts people expect (alerts to myself, reminders, sign-in codes, order updates).
```

## Responsible use

Send only texts that people expect from you: alerts to yourself, appointment reminders, sign-in codes, order updates. Unsolicited messages from a personal SIM break most carriers' terms of service. Please make sure your usage complies with your carrier's terms and with local regulations (in the EU, that includes GDPR and ePrivacy rules on electronic communications).

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e . --group dev   # pip 25.1 or newer
pytest                         # unit tests
ruff check . && ruff format --check .
mypy                           # strict
```

The end-to-end tests run only against a test server, never production. They read the server address and a test key from a JSON file (`{"serverUrl": "http://127.0.0.1:3101", "apiKey": "...", "deviceId": "..."}`) and, when `SMSTUNNEL_E2E_MONGO_URL` is set, check each text in the server's database:

```bash
pip install --group e2e
SMSTUNNEL_E2E_KEY_FILE=/path/to/test-key.json SMSTUNNEL_E2E_MONGO_URL=mongodb://127.0.0.1:27018/smstunnel_app_dev pytest tests/e2e
```

## Changelog

See [CHANGELOG.md](https://github.com/narcisbodea/smstunnel-python/blob/main/CHANGELOG.md).

## License

MIT, © NARBOWEB SRL. See [LICENSE](https://github.com/narcisbodea/smstunnel-python/blob/main/LICENSE).
