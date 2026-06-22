"""Test yardımcıları ve paylaşılan fixture'lar."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def wait_for(predicate, timeout=8.0, interval=0.05):
    """predicate() True dönene kadar (veya timeout) bekler. Sonucu döner."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _CaptureHandler(BaseHTTPRequestHandler):
    """Gelen istekleri sunucunun ``requests`` listesine kaydeder."""

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body.decode("utf-8") if body else "",
        }
        try:
            record["json"] = json.loads(body) if body else None
        except json.JSONDecodeError:
            record["json"] = None
        self.server.requests.append(record)  # type: ignore[attr-defined]

        status = self.server.next_status  # type: ignore[attr-defined]
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle

    def log_message(self, *args):  # noqa: D401 - test sunucusu sessiz olsun
        pass


class MockServer:
    """Test içinde gelen HTTP isteklerini yakalayan basit sunucu."""

    def __init__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
        self._httpd.requests = []  # type: ignore[attr-defined]
        self._httpd.next_status = 200  # type: ignore[attr-defined]
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self) -> list:
        return self._httpd.requests  # type: ignore[attr-defined]

    def set_status(self, status: int) -> None:
        self._httpd.next_status = status  # type: ignore[attr-defined]

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def mock_server():
    server = MockServer().start()
    try:
        yield server
    finally:
        server.stop()
