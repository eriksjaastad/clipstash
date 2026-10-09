"""Shared test helpers: HTTP requests (JSON or multipart) against a test server and a 1-px PNG."""

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


def post_multipart_packet(httpd, image: bytes = PNG_1PX, path="/packets", part="image", **fields: str):
    """POST *path* as multipart/form-data: text *fields* plus one *part* file part."""
    boundary = "clipstash-test-boundary"
    text = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    )
    part = f'--{boundary}\r\nContent-Disposition: form-data; name="{part}"; filename="still.png"\r\n\r\n'
    body = (text + part).encode("utf-8") + image + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return request(server_url(httpd, path), data=body, method="POST", headers=headers)
