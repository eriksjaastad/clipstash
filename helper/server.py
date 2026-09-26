"""Local HTTP server for clipstash.

Binds 127.0.0.1 only. Endpoints:

  GET  /health     -> {"ok": true, "version": "..."}
  POST /packets    -> write a packet (JSON+base64 or multipart), return record
  GET  /packets    -> list packet summaries
  GET  /export.csv -> CSV of all packets
"""

from __future__ import annotations

import base64
import binascii
import json
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .packets import export_csv, list_packets, new_record, write_packet

JSON = "application/json"


def _decode_image_b64(value: str) -> bytes:
    """Decode a base64 image, accepting an optional data: URL prefix."""
    if not value:
        raise ValueError("image_base64 is required")
    if "," in value and value.lstrip().startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"invalid image_base64: {exc}") from exc


def _record_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        record = new_record(
            title=str(payload["title"]),
            source_url=str(payload["source_url"]),
            page_url=payload.get("page_url") or None,
            site=str(payload.get("site") or "generic"),
            timestamp_sec=payload.get("timestamp_sec"),
            tags=payload.get("tags") or [],
            notes=str(payload.get("notes") or ""),
            capture_method=str(payload.get("capture_method") or "canvas"),
        )
    except KeyError as exc:
        raise ValueError(f"missing field: {exc.args[0]}") from exc
    image = _decode_image_b64(str(payload.get("image_base64") or ""))
    return record, image


def _parse_multipart(content_type: str, body: bytes) -> dict[str, Any]:
    """Parse multipart/form-data into {field: str, files: {name: bytes}}."""
    raw = b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
    message = BytesParser(policy=email_policy).parsebytes(raw)
    fields: dict[str, Any] = {}
    files: dict[str, bytes] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = payload
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace")
    return {"fields": fields, "files": files}


def _record_from_multipart(content_type: str, body: bytes) -> dict[str, Any]:
    parsed = _parse_multipart(content_type, body)
    fields = parsed["fields"]
    files = parsed["files"]
    image = files.get("image") or files.get("still")
    if image is None:
        image = _decode_image_b64(str(fields.get("image_base64") or ""))
    try:
        record = new_record(
            title=str(fields["title"]),
            source_url=str(fields["source_url"]),
            page_url=fields.get("page_url") or None,
            site=str(fields.get("site") or "generic"),
            timestamp_sec=_optional_float(fields.get("timestamp_sec")),
            tags=json.loads(fields.get("tags") or "[]"),
            notes=str(fields.get("notes") or ""),
            capture_method=str(fields.get("capture_method") or "canvas"),
        )
    except KeyError as exc:
        raise ValueError(f"missing field: {exc.args[0]}") from exc
    return record, image


def _optional_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


class ClipStashHandler(BaseHTTPRequestHandler):
    server_version = "clipstashd/" + __version__
    root: str | None = None

    # -- plumbing ---------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), JSON)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    # -- endpoints --------------------------------------------------------
    def do_OPTIONS(self):  # noqa: N802 (stdlib name)
        self._send(204, b"", "text/plain")

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json({"ok": True, "version": __version__})
        elif path == "/packets":
            self._send_json({"ok": True, "packets": list_packets(self.root)})
        elif path == "/export.csv":
            self._send(200, export_csv(self.root).encode("utf-8"), "text/csv")
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path != "/packets":
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        content_type = self.headers.get("Content-Type") or ""
        body = self._read_body()
        try:
            if content_type.startswith("multipart/form-data"):
                record, image = _record_from_multipart(content_type, body)
            elif content_type.startswith(JSON):
                payload = json.loads(body.decode("utf-8") or "{}")
                record, image = _record_from_payload(payload)
            else:
                self._send_json(
                    {"ok": False, "error": "expected JSON or multipart/form-data"}, 415
                )
                return
            written = write_packet(record, image, root=self.root)
            self._send_json({"ok": True, "packet": written}, 201)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - last-resort guard
            self._send_json({"ok": False, "error": f"internal error: {exc}"}, 500)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[clipstashd] {self.address_string()} - {fmt % args}")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    root: str | None = None,
) -> ThreadingHTTPServer:
    """Build a server bound to host (must stay loopback-only in normal use)."""
    handler = type("BoundClipStashHandler", (ClipStashHandler,), {"root": root})
    return ThreadingHTTPServer((host, port), handler)


def run_server(host: str = "127.0.0.1", port: int = 8787, root: str | None = None) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("clipstashd refuses to bind to non-loopback interfaces")
    server = create_server(host=host, port=port, root=root)
    actual = server.server_address[1]
    print(f"clipstashd {__version__} listening on http://{host}:{actual} (root: {root or '~/Clipstash/packets'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nclipstashd stopped")
    finally:
        server.server_close()
