# Changelog

All notable changes to `smstunnel` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

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
