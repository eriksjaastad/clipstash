"""Local HTTP server for clipstash.

Binds 127.0.0.1 only. Endpoints:

  GET  /health              -> {"ok": true, "version": "..."}
  GET  /config              -> {"ok": true, "packet_root": "...", "default_root": "...", "config_path": "..."}
  PUT  /config              -> set packet_root, persist config.json, update live root
  POST /packets             -> write a packet (JSON+base64 or multipart), return record
  POST /packets/{id}/place-photoshop
                            -> place the packet's still into Photoshop (macOS)
  GET  /packets             -> list packet summaries
  GET  /export.csv          -> CSV of all packets
  POST /bursts              -> create a burst session, return {session_id, picker_url}
  GET  /picker/<id>         -> HTML grid of burst frames
  GET  /picker/<id>/frame/N -> PNG for frame N
  POST /picker/<id>/choose  -> write chosen frame as a normal packet
  GET  /history/pending     -> drain pending burst-chosen history entries
"""

from __future__ import annotations

import base64
import binascii
import html
import json
import re
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .bursts import (
    choose_frame,
    create_burst,
    frame_filename,
    load_session,
    purge_expired_bursts,
    session_dir,
)
from .config import (
    default_config_path,
    default_packet_root,
    effective_packet_root,
    load_config,
    normalize_packet_root,
    save_config,
)
from .packets import (
    export_csv,
    image_path as packet_image_path,
    list_packets,
    new_record,
    packet_dir,
    read_record,
    write_packet,
)
from .photoshop import env_photoshop_enabled, place_in_photoshop

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


def _as_bool(value: Any) -> bool:
    """Accept JSON booleans and common string forms of truthiness."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _record_from_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bytes, bool]:
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
    return record, image, _as_bool(payload.get("photoshop"))


def _parse_multipart(content_type: str, body: bytes) -> dict[str, Any]:
    """Parse multipart/form-data into {field: str, files: {name: bytes}, file_list: [(name, filename, bytes)]}."""
    raw = b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
    message = BytesParser(policy=email_policy).parsebytes(raw)
    fields: dict[str, Any] = {}
    files: dict[str, bytes] = {}
    file_list: list[tuple[str, str, bytes]] = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = payload
            file_list.append((name, filename, payload))
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace")
    return {"fields": fields, "files": files, "file_list": file_list}


def _record_from_multipart(content_type: str, body: bytes) -> tuple[dict[str, Any], bytes, bool]:
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
    return record, image, _as_bool(fields.get("photoshop"))


def _optional_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _history_entry_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Map a packet record to the extension history entry shape.

    Mirrors the entry built by the extension's ``pushHistory`` path in
    ``background.js`` so burst-chosen packets land in history with the same
    shape as single captures: ``{id, title, url, text, createdAt}``.
    """
    clipboard = record.get("clipboard") or {}
    text = clipboard.get("text") if isinstance(clipboard, dict) else ""
    if not text:
        text = f"{record.get('title', '')}\n{record.get('source_url', '')}"
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "url": record.get("source_url"),
        "text": text,
        "createdAt": record.get("created_at"),
    }


# -- burst sessions -----------------------------------------------------------


def _burst_metadata_from_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(fields.get("title") or ""),
        "source_url": str(fields.get("source_url") or ""),
        "page_url": str(fields.get("page_url") or fields.get("source_url") or ""),
        "site": str(fields.get("site") or "generic"),
        "timestamp_sec": _optional_float(fields.get("timestamp_sec")),
        "capture_method": str(fields.get("capture_method") or "burst_canvas"),
        "photoshop": _as_bool(fields.get("photoshop")),
    }


def _burst_from_payload(payload: dict[str, Any]) -> tuple[list[bytes], dict[str, Any]]:
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ValueError("burst requires a non-empty frames array of data URLs")
    frames = [_decode_image_b64(str(frame)) for frame in raw_frames]
    metadata = _burst_metadata_from_fields(payload)
    metadata["photoshop"] = _as_bool(payload.get("photoshop"))
    return frames, metadata


def _frame_sort_key(indexed: tuple[int, tuple[str, str, bytes]]) -> tuple[int, int, int]:
    position, (name, _filename, _payload) = indexed
    match = re.fullmatch(r"frame_?(\d*)", name)
    if match and match.group(1).isdigit():
        return (0, int(match.group(1)), position)
    if name in ("frames", "frame"):
        return (1, position, position)
    return (2, position, position)


def _burst_from_multipart(content_type: str, body: bytes) -> tuple[list[bytes], dict[str, Any]]:
    parsed = _parse_multipart(content_type, body)
    file_list = [
        (name, filename, payload)
        for name, filename, payload in parsed["file_list"]
        if name not in ("image", "still")
    ]
    if not file_list:
        raise ValueError("burst requires at least one frame file")
    ordered = sorted(enumerate(file_list), key=_frame_sort_key)
    frames = [payload for _position, (_name, _filename, payload) in ordered]
    return frames, _burst_metadata_from_fields(parsed["fields"])


def _json_for_script(value: Any) -> str:
    """JSON-serialize a value for safe embedding inside an inline <script>."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _picker_html(session_id: str, meta: dict[str, Any]) -> str:
    frame_count = int(meta.get("frame_count") or 0)
    cards = "\n".join(
        f'      <button type="button" class="frame" data-index="{index}">'
        f'<img src="/picker/{session_id}/frame/{index}" alt="frame {index}" />'
        f"</button>"
        for index in range(frame_count)
    )
    meta_json = _json_for_script(
        {
            "title": meta.get("title") or "",
            "source_url": meta.get("source_url") or "",
            "page_url": meta.get("page_url") or meta.get("source_url") or "",
            "site": meta.get("site") or "generic",
            "timestamp_sec": meta.get("timestamp_sec"),
            "capture_method": meta.get("capture_method") or "burst_canvas",
        }
    )
    meta_text = html.escape(
        json.dumps(
            {
                "title": meta.get("title") or "",
                "url": meta.get("source_url") or "",
            }
        )[1:-1]
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>clipstash — burst picker</title>
    <style>
      body {{
        margin: 0;
        padding: 24px;
        background: #141414;
        color: #f5f2ea;
        font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      h1 {{ font-size: 18px; margin: 0 0 4px; }}
      .meta {{ color: #9a948a; font-size: 12px; margin: 0 0 16px; word-break: break-all; }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
        gap: 10px;
      }}
      .frame {{
        border: 2px solid #2a2a2a;
        border-radius: 10px;
        overflow: hidden;
        padding: 0;
        background: #000;
        cursor: pointer;
      }}
      .frame:hover {{ border-color: #2f6fed; }}
      .frame:disabled {{ opacity: 0.35; cursor: default; }}
      .frame img {{ display: block; width: 100%; height: auto; }}
      .status {{ margin: 16px 0 0; color: #9a948a; }}
      .status.ok {{ color: #1f9d55; }}
      .status.err {{ color: #e06c5a; }}
    </style>
  </head>
  <body>
    <h1>Burst &amp; pick — choose the best frame</h1>
    <p class="meta">{meta_text}</p>
    <div class="grid">
{cards}
    </div>
    <p id="status" class="status">Click a frame to save it as a packet.</p>
    <script>
      const SESSION_ID = {_json_for_script(session_id)};
      const META = {meta_json};

      document.querySelectorAll(".frame").forEach((button) => {{
        button.addEventListener("click", () => choose(Number(button.dataset.index)));
      }});

      async function choose(index) {{
        const status = document.getElementById("status");
        status.textContent = "saving…";
        status.className = "status";
        try {{
          const response = await fetch(`/picker/${{SESSION_ID}}/choose`, {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ frame_index: index, ...META }}),
          }});
          const payload = await response.json();
          if (payload.ok) {{
            const placed = payload.photoshop
              ? (payload.photoshop.ok ? " · Photoshop: placed" : ` · Photoshop: ${{payload.photoshop.error || "not placed"}}`)
              : "";
            status.textContent = `Saved packet ${{payload.packet.id}}${{placed}}`;
            status.className = "status ok";
            document.querySelectorAll(".frame").forEach((button) => (button.disabled = true));
            window.dispatchEvent(new CustomEvent("clipstash:chosen", {{ detail: payload.packet }}));
          }} else {{
            status.textContent = payload.error || "choose failed";
            status.className = "status err";
          }}
        }} catch (error) {{
          status.textContent = `choose failed: ${{error}}`;
          status.className = "status err";
        }}
      }}
    </script>
  </body>
</html>
"""


class ClipStashHandler(BaseHTTPRequestHandler):
    server_version = "clipstashd/" + __version__
    root: str | None = None
    config_path: str | None = None
    photoshop_auto: bool = False
    pending_history: list[dict[str, Any]] = []
    pending_history_lock: threading.Lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
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

    def _enqueue_pending_history(self, entry: dict[str, Any]) -> None:
        with self.pending_history_lock:
            self.pending_history.append(entry)

    def _serve_pending_history(self) -> None:
        with self.pending_history_lock:
            entries = list(self.pending_history)
            self.pending_history.clear()
        self._send_json({"ok": True, "entries": entries})

    # -- endpoints --------------------------------------------------------
    def do_OPTIONS(self):  # noqa: N802 (stdlib name)
        self._send(204, b"", "text/plain")

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._send_json({"ok": True, "version": __version__})
            elif path == "/config":
                self._serve_config()
            elif path == "/packets":
                self._send_json({"ok": True, "packets": list_packets(self.root)})
            elif path == "/export.csv":
                self._send(200, export_csv(self.root).encode("utf-8"), "text/csv")
            elif path == "/history/pending":
                self._serve_pending_history()
            else:
                match = re.fullmatch(r"/picker/([^/]+)", path)
                if match:
                    self._serve_picker(match.group(1))
                    return
                match = re.fullmatch(r"/picker/([^/]+)/frame/(\d+)", path)
                if match:
                    self._serve_frame(match.group(1), int(match.group(2)))
                    return
                self._send_json({"ok": False, "error": "not found"}, 404)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, 404)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        content_type = self.headers.get("Content-Type") or ""
        body = self._read_body()
        try:
            if path == "/packets":
                self._create_packet(content_type, body)
                return
            if path == "/bursts":
                self._create_burst_session(content_type, body)
                return
            match = re.fullmatch(r"/packets/([^/]+)/place-photoshop", path)
            if match:
                self._place_packet_in_photoshop(match.group(1))
                return
            match = re.fullmatch(r"/picker/([^/]+)/choose", path)
            if match:
                self._choose_burst_frame(match.group(1), body)
                return
            self._send_json({"ok": False, "error": "not found"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, 404)
        except Exception as exc:  # pragma: no cover - last-resort guard
            self._send_json({"ok": False, "error": f"internal error: {exc}"}, 500)

    def do_PUT(self):  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_body()
        try:
            if path == "/config":
                self._update_config(body)
                return
            self._send_json({"ok": False, "error": "not found"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except OSError as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - last-resort guard
            self._send_json({"ok": False, "error": f"internal error: {exc}"}, 500)

    # -- action handlers ---------------------------------------------------
    def _config_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "packet_root": str(Path(self.root or default_packet_root()).expanduser().resolve()),
            "default_root": str(default_packet_root()),
            "config_path": str(self.config_path or default_config_path()),
        }

    def _serve_config(self) -> None:
        self._send_json(self._config_payload())

    def _update_config(self, body: bytes) -> None:
        payload = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        path = normalize_packet_root(payload.get("packet_root"))
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"cannot create packet root {str(path)!r}: {exc}") from exc
        config_path = self.config_path or str(default_config_path())
        config = load_config(config_path)
        config["packet_root"] = str(path)
        save_config(config, config_path)
        type(self).root = str(path)
        self._send_json(self._config_payload())

    def _photoshop_requested(self, explicit: Any) -> bool:
        return _as_bool(explicit) or self.photoshop_auto or env_photoshop_enabled()

    def _create_packet(self, content_type: str, body: bytes) -> None:
        if content_type.startswith("multipart/form-data"):
            record, image, photoshop = _record_from_multipart(content_type, body)
        elif content_type.startswith(JSON):
            payload = json.loads(body.decode("utf-8") or "{}")
            record, image, photoshop = _record_from_payload(payload)
        else:
            self._send_json(
                {"ok": False, "error": "expected JSON or multipart/form-data"}, 415
            )
            return
        written = write_packet(record, image, root=self.root)
        response: dict[str, Any] = {"ok": True, "packet": written}
        if self._photoshop_requested(photoshop):
            response["photoshop"] = place_in_photoshop(
                packet_image_path(packet_dir(self.root, str(written["id"])))
            )
        self._send_json(response, 201)

    def _create_burst_session(self, content_type: str, body: bytes) -> None:
        if content_type.startswith("multipart/form-data"):
            frames, metadata = _burst_from_multipart(content_type, body)
        elif content_type.startswith(JSON):
            payload = json.loads(body.decode("utf-8") or "{}")
            frames, metadata = _burst_from_payload(payload)
        else:
            self._send_json(
                {"ok": False, "error": "expected JSON or multipart/form-data"}, 415
            )
            return
        meta = create_burst(frames, metadata)
        session_id = str(meta["session_id"])
        self._send_json(
            {
                "ok": True,
                "session_id": session_id,
                "picker_url": self._picker_url(session_id),
                "frame_count": int(meta.get("frame_count") or 0),
            },
            201,
        )

    def _serve_picker(self, session_id: str) -> None:
        purge_expired_bursts()
        meta = load_session(session_id)
        html = _picker_html(session_id, meta).encode("utf-8")
        self._send(200, html, "text/html; charset=utf-8")

    def _serve_frame(self, session_id: str, index: int) -> None:
        meta = load_session(session_id)
        frame_count = int(meta.get("frame_count") or 0)
        if index < 0 or index >= frame_count:
            self._send_json({"ok": False, "error": "frame not found"}, 404)
            return
        path = session_dir(session_id) / frame_filename(index)
        if not path.exists():
            self._send_json({"ok": False, "error": "frame not found"}, 404)
            return
        self._send(200, path.read_bytes(), "image/png")

    def _choose_burst_frame(self, session_id: str, body: bytes) -> None:
        payload = json.loads(body.decode("utf-8") or "{}")
        try:
            frame_index = int(payload.get("frame_index"))
        except (TypeError, ValueError) as exc:
            raise ValueError("frame_index is required") from exc
        meta = load_session(session_id)
        photoshop = self._photoshop_requested(
            _as_bool(payload.get("photoshop")) or _as_bool(meta.get("photoshop"))
        )
        record = choose_frame(session_id, frame_index, payload, root=self.root)
        self._enqueue_pending_history(_history_entry_from_record(record))
        response: dict[str, Any] = {"ok": True, "packet": record}
        if photoshop:
            response["photoshop"] = place_in_photoshop(
                packet_image_path(packet_dir(self.root, str(record["id"])))
            )
        self._send_json(response, 200)

    def _place_packet_in_photoshop(self, packet_id: str) -> None:
        record = read_record(packet_id, root=self.root)
        directory = packet_dir(self.root, packet_id)
        image = directory / str(record.get("image") or "still.png")
        if not image.exists():
            raise FileNotFoundError(f"packet {packet_id!r} has no still image")
        result = place_in_photoshop(image)
        response: dict[str, Any] = {
            "ok": bool(result.get("ok")),
            "packet_id": packet_id,
            "photoshop": result,
        }
        if not result.get("ok"):
            response["error"] = result.get("error") or "Photoshop place failed"
        self._send_json(response, 200)

    def _picker_url(self, session_id: str) -> str:
        host_header = self.headers.get("Host")
        if host_header:
            return f"http://{host_header}/picker/{session_id}"
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/picker/{session_id}"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[clipstashd] {self.address_string()} - {fmt % args}")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    root: str | None = None,
    photoshop_auto: bool = False,
    config_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    """Build a server bound to host (must stay loopback-only in normal use)."""
    config_path_obj = Path(config_path) if config_path else default_config_path()
    effective_root = effective_packet_root(root, config_path=config_path_obj)
    handler = type(
        "BoundClipStashHandler",
        (ClipStashHandler,),
        {
            "root": str(effective_root),
            "config_path": str(config_path_obj),
            "photoshop_auto": photoshop_auto,
            "pending_history": [],
            "pending_history_lock": threading.Lock(),
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    root: str | None = None,
    photoshop_auto: bool = False,
    config_path: str | Path | None = None,
) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("clipstashd refuses to bind to non-loopback interfaces")
    config_path_obj = Path(config_path) if config_path else default_config_path()
    effective_root = effective_packet_root(root, config_path=config_path_obj)
    server = create_server(
        host=host,
        port=port,
        root=root,
        photoshop_auto=photoshop_auto,
        config_path=config_path_obj,
    )
    actual = server.server_address[1]
    print(f"clipstashd {__version__} listening on http://{host}:{actual} (root: {effective_root})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nclipstashd stopped")
    finally:
        server.server_close()
