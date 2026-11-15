# Changelog

All notable changes to `smstunnel` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-11-15

### Added

- **Scheduled texts.** `send_sms()` takes `send_at`, in `Client` and `AsyncClient`: a timezone-aware `datetime`, sent
  as its `isoformat()` to the second with its own offset, or an ISO 8601 string with an offset, 1 minute to 30 days
  ahead. SMSTunnel keeps the text and hands it to your phone at that time, as if your app called `send_sms()` then
  with the same key. The call then returns a `smstunnel.ScheduledText` (`id`, `send_at`, `status`, `message_id` once
  it went out, `error` when it failed, `replayed`) instead of a `SendResult`. A naive `datetime` or a string without
  an offset raises `ValueError` before anything is sent.
- `list_scheduled(scope, page=..., limit=...)`: the texts this key scheduled, `"waiting"` (soonest first) or `"done"`
  (newest first, kept 30 days), as `ScheduledTexts` with `waiting` and `max` for the plan's cap. `get_scheduled(id)`
  and `cancel_scheduled(id)` (a `DELETE`). All three in `AsyncClient` too.
- Their errors keep SMSTunnel's code in `error_code`: `SCHEDULE_TOO_SOON`, `SCHEDULE_TOO_FAR` and the other
  `SCHEDULE_*` codes; 403 `SCHEDULE_LIMIT_REACHED` (a `PermissionDeniedError`, with `max` in `body`), 404
  `SCHEDULE_NOT_FOUND`, 409 `SCHEDULE_NOT_EDITABLE`.

### Changed

- `send_sms()` is typed with `@overload`: without `send_at` (or with `None`) it returns a `SendResult`, as before; with
  one, a `ScheduledText`. Type checkers see `SendResult` for every existing call.
- Transports also get `DELETE` requests (`cancel_scheduled()`); the built-in ones already send them.

Scheduling needs SMSTunnel with scheduled texts; a server without them answers 400 "property sendAt should not exist"
(an `ApiError`).

## [1.1.0] - not released on its own (these changes ship in 1.2.0)

### Added

- **Safe retries: `idempotency_key`** on `send_sms()` and `send_verification_code()`, in `Client` and `AsyncClient`.
  It goes out as the `Idempotency-Key` header. For 24 hours, a repeat of the same call with the same key gets the
  first answer back (the same `message_id`) instead of a second text; the same key with a different text, number,
  phone or country code is refused with an `ApiError` (422, `error_code` `IDEMPOTENCY_KEY_REUSED`). Use one new key
  per text and the same key on every retry of it. A key SMSTunnel can't take (empty, over 255 characters, not
  printable ASCII, spaces at the ends) raises `ValueError`, and one that is not a `str` raises `TypeError`, before
  anything is sent.
- `SendResult.replayed`: `True` when SMSTunnel answered a repeat of the key with the first answer (the
  `Idempotent-Replayed: true` header), so your app can tell that the call sent no new text.
  `SendResult.from_dict()` takes it as the keyword argument `replayed`.
- `ApiError.retry_after`, moved up from `RateLimitError` (which still has it), so a 409 with `error_code`
  `IDEMPOTENCY_KEY_IN_PROGRESS` (the first try with the same key is still running) says how long to wait too.

The client still never retries on its own. Without `idempotency_key`, every call works as in 1.0.0.

## [1.0.0] - 2026-11-01

### Added

- `smstunnel.Client` with `send_sms()`, `send_verification_code()`, `get_message_status()`,
  `get_received_messages()`, `get_devices()` and `get_usage()`, and `smstunnel.AsyncClient` with the same methods
  as coroutines.
- Typed, read-only responses: `SendResult`, `MessageStatus`, `ReceivedMessages` / `ReceivedMessage`,
  `DeviceList` / `Device`, `Usage`. Times are timezone-aware `datetime` objects.
- Exceptions: `ApiError` with the HTTP status, SMSTunnel's error code and message, and the subclasses
  `AuthenticationError` (401), `PermissionDeniedError` (403) and `RateLimitError` (429, with `retry_after`);
  `TransportError` when SMSTunnel cannot be reached. All extend `SmsTunnelError`.
- A standard-library transport (no dependencies, never follows redirects), and optional httpx transports
  (`pip install "smstunnel[httpx]"`).
