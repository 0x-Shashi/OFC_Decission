"""The local service refuses bad request sizes and sends anti-framing headers."""

from __future__ import annotations

import http.client
import threading
from http import HTTPStatus

import pytest

from quartermaster.service.api import ApiError, Reply, _body_length, make_server


class _App:
    def handle(self, method: str, raw_path: str, body: bytes, cookie: str = "") -> Reply:
        return Reply(HTTPStatus.OK, b"ok", "text/plain")


def test_body_length_rules() -> None:
    assert _body_length(None) == 0
    assert _body_length("12") == 12
    for bad in ("abc", "-1", str(10**12)):
        with pytest.raises(ApiError):
            _body_length(bad)


def test_headers_and_huge_body() -> None:
    server = make_server(_App(), port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", port)
        c.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        r = c.getresponse()
        r.read()
        assert r.getheader("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in (r.getheader("Content-Security-Policy") or "")
        assert r.getheader("Referrer-Policy") == "no-referrer"
        c.close()
        c = http.client.HTTPConnection("127.0.0.1", port)
        c.request(
            "POST",
            "/",
            headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "Content-Length": str(10**12)},
        )
        assert c.getresponse().status == 413
        c.close()
    finally:
        server.shutdown()
