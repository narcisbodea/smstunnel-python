"""The standard-library transport against a small local HTTP server."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from smstunnel import AuthenticationError, Client, TransportError
from smstunnel.transport import HttpRequest, UrllibTransport

from .support import KEY


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.route()

    def do_POST(self) -> None:
        self.route()

    def route(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else None
        if self.path.startswith("/echo"):
            self.reply(
                200,
                {
                    "success": True,
                    "data": {
                        "method": self.command,
                        "path": self.path,
                        "headers": {name.lower(): value for name, value in self.headers.items()},
                        "body": body,
                    },
                },
            )
        elif self.path == "/api/v1/devices":
            self.reply(401, {"message": "Invalid or expired API key", "error": "Unauthorized", "statusCode": 401})
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "https://elsewhere.example/steal")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/slow":
            time.sleep(2)
            self.reply(200, {"success": True})
        elif self.path == "/latin1":
            payload = "Café fermé".encode("latin-1")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=iso-8859-1")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.reply(404, {"message": "Cannot GET " + self.path, "statusCode": 404})

    def reply(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Test", "yes")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def request(url: str, method: str = "GET", body: bytes | None = None, timeout: float = 5.0) -> HttpRequest:
    headers = {"X-API-Key": KEY, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    return HttpRequest(method, url, headers, body, timeout)


def test_sends_the_method_headers_and_body(server: str) -> None:
    answer = UrllibTransport().send(request(server + "/echo", "POST", b'{"to":"+14155550123"}'))

    assert answer.status_code == 200
    assert answer.header("X-Test") == "yes"
    echoed = json.loads(answer.text)["data"]
    assert echoed["method"] == "POST"
    assert echoed["headers"]["x-api-key"] == KEY
    assert echoed["headers"]["content-type"] == "application/json"
    assert echoed["body"] == '{"to":"+14155550123"}'


def test_hands_back_an_error_status_as_an_answer(server: str) -> None:
    answer = UrllibTransport().send(request(server + "/nothing-here"))

    assert answer.status_code == 404
    assert json.loads(answer.text)["message"] == "Cannot GET /nothing-here"


def test_never_follows_a_redirect(server: str) -> None:
    answer = UrllibTransport().send(request(server + "/redirect"))

    assert answer.status_code == 302
    assert answer.header("location") == "https://elsewhere.example/steal"


def test_decodes_the_charset_the_server_names(server: str) -> None:
    answer = UrllibTransport().send(request(server + "/latin1"))

    assert answer.text == "Café fermé"


def test_a_timeout_is_a_transport_error(server: str) -> None:
    with pytest.raises(TransportError, match=r"no answer within 0\.5 s"):
        UrllibTransport().send(request(server + "/slow", timeout=0.5))


def test_a_closed_port_is_a_transport_error() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Nothing listens on the port any more.
    with pytest.raises(TransportError, match=f"Could not reach SMSTunnel at http://127.0.0.1:{port}"):
        UrllibTransport().send(request(f"http://127.0.0.1:{port}/api/v1/devices", timeout=3))


def test_the_client_end_to_end_over_http(server: str) -> None:
    client = Client(KEY, server + "/echo")

    with pytest.raises(AuthenticationError, match="Invalid or expired API key"):
        Client(KEY, server).get_devices()

    # The echo route answers any path below it, so the request can be checked as the server saw it.
    usage = client.get_usage()
    assert usage.raw["method"] == "GET"
    assert usage.raw["path"] == "/echo/api/v1/account/usage"
    assert usage.raw["headers"]["x-api-key"] == KEY
    assert usage.raw["headers"]["user-agent"].startswith("smstunnel-python/")


def test_the_idempotency_key_reaches_the_server(server: str) -> None:
    client = Client(KEY, server + "/echo")

    sent = client.send_sms("+14155550123", "Hi", idempotency_key="order-1042-shipped").raw["data"]
    plain = client.send_sms("+14155550123", "Hi").raw["data"]

    assert sent["path"] == "/echo/api/v1/sms/send"
    assert sent["headers"]["idempotency-key"] == "order-1042-shipped"
    assert "idempotency-key" not in plain["headers"]
