"""Shared test helpers: HTTP requests against a test server and a 1-px PNG."""

from __future__ import annotations

import base64
import urllib.error
import urllib.request

PNG_1PX_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
PNG_1PX = base64.b64decode(PNG_1PX_B64)


def request(url: str, data: bytes | None = None, method: str | None = None, headers: dict | None = None):
    """Return (status, content_type, body), including for HTTP error statuses."""
    headers = dict(headers or {})
    if method in ("POST", "PUT") and data is not None and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read()


def server_url(httpd, path: str) -> str:
    host, port = httpd.server_address
    return f"http://{host}:{port}{path}"
