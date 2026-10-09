"""Local HTTP server for clipstash.

Binds 127.0.0.1 only. Endpoints:

  GET  /health              -> {"ok": true, "version": "..."}
  GET  /config              -> {"ok": true, "packet_root": "...", "default_root": "...", "config_path": "..."}
  PUT  /config              -> set packet_root, persist config.json, update live root
  POST /packets             -> write a packet (JSON+base64 or multipart), return record
                               (optional ``name`` field = still-name override,
                               always re-slugified server-side)
  POST /packets/{id}/place-photoshop
                            -> place the packet's still into Photoshop (macOS)
  GET  /packets             -> list packet summaries
  GET  /export.csv          -> CSV of all packets
  POST /bursts              -> create a burst session, return {session_id, picker_url}
  POST /bursts/ffmpeg       -> create a native ffmpeg burst from a media URL
                               (tainted-canvas path; ffmpeg on PATH required)
  GET  /picker/<id>         -> HTML grid of burst frames
  GET  /picker/<id>/frame/N -> PNG for frame N
  POST /picker/<id>/choose  -> write chosen frame as a normal packet
  GET  /history/pending     -> drain pending burst-chosen history entries

Both ``POST /packets`` and ``POST /bursts`` accept an optional ``crop_rect``
request field (JSON object, or JSON string in a multipart ``POST /packets``):
``{x, y, width, height, dpr}`` in CSS viewport pixels. The extension sends it
on tainted ``visible_tab`` / ``burst_visible_tab`` captures so the full-tab
PNG can be cropped to the video element's on-screen rectangle before it is
saved (see ``helper.crop``). The field is ephemeral and is never persisted
into ``record.yaml`` or burst metadata.

Both also accept an optional ``name`` field: a type-in override for the still
file name. It is trimmed by the client, slugified server-side, and used as
``<slug>.png`` under ``<root>/<site>/<id>/`` (see ``helper.packets``). An
empty or omitted ``name`` falls back to the slugified video title, and an
unusable title falls back to ``<site>-<id[:8]>``.
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
from .coerce import as_bool, optional_float, packet_fields
from .config import (
    ConfigError,
    default_config_path,
    default_packet_root,
    effective_packet_root,
    load_config,
    normalize_packet_root,
    save_config,
)
from .crop import apply_crop_rect
from .ffmpeg_burst import FFMPEG_MISSING, FFmpegBurstError, burst_frames_from_url, ffmpeg_path
from .packets import (
    export_csv,
    list_packets,
    new_record,
    packet_image_path,
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


def _parse_json_object(value: Any) -> dict[str, Any] | None:
    """Parse a multipart crop_rect field (JSON string) into a dict, or None."""
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):  # governance: allow-silent SF002: an unusable crop_rect means "no crop", the same contract apply_crop_rect has for invalid rects (test_apply_crop_rect_invalid_rects_return_original_bytes)
        return None
    return parsed if isinstance(parsed, dict) else None


def _packet_record(fields: dict[str, Any], *, form_data: bool) -> dict[str, Any]:
    """Build the ``POST /packets`` record; new_record checks title/source_url and fills page_url.

    Form-data values are text, so ``timestamp_sec`` and ``tags`` are parsed
    from it; a JSON body stores them as sent.
    """
    try:
        return new_record(
            title=str(fields["title"]),
            source_url=str(fields["source_url"]),
            page_url=fields.get("page_url") or None,
            site=str(fields.get("site") or "generic"),
            timestamp_sec=(
                optional_float(fields.get("timestamp_sec"))
                if form_data
                else fields.get("timestamp_sec")
            ),
            tags=json.loads(fields.get("tags") or "[]") if form_data else fields.get("tags") or [],
            notes=str(fields.get("notes") or ""),
            capture_method=str(fields.get("capture_method") or "canvas"),
            name=str(fields.get("name") or "") or None,
        )
    except KeyError as exc:
        raise ValueError(f"missing field: {exc.args[0]}") from exc


def _record_from_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bytes, bool]:
    record = _packet_record(payload, form_data=False)
    image = _decode_image_b64(str(payload.get("image_base64") or ""))
    image = apply_crop_rect(image, payload.get("crop_rect"))
    return record, image, as_bool(payload.get("photoshop"))


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, bytes]]:
    """Parse multipart/form-data into ({field: text}, {name: file bytes})."""
    raw = b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
    message = BytesParser(policy=email_policy).parsebytes(raw)
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if part.get_filename():
            files[name] = payload
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace")
    return fields, files


def _record_from_multipart(
    fields: dict[str, str], files: dict[str, bytes]
) -> tuple[dict[str, Any], bytes, bool]:
    image = files.get("image") or files.get("still")
    if image is None:
        image = _decode_image_b64(str(fields.get("image_base64") or ""))
    image = apply_crop_rect(image, _parse_json_object(fields.get("crop_rect")))
    return _packet_record(fields, form_data=True), image, as_bool(fields.get("photoshop"))


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


def _burst_from_payload(payload: dict[str, Any]) -> tuple[list[bytes], dict[str, Any]]:
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ValueError("burst requires a non-empty frames array of data URLs")
    crop_rect = payload.get("crop_rect")
    frames = [
        apply_crop_rect(_decode_image_b64(str(frame)), crop_rect) for frame in raw_frames
    ]
    return frames, packet_fields(payload, strict_timestamp=True)


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
    # META is posted back to choose; the photoshop flag stays in the session.
    fields = packet_fields(meta)
    del fields["photoshop"]
    meta_json = _json_for_script(fields)
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


class _UnsupportedContentType(Exception):
    """The request body is neither JSON nor a form the route accepts (415)."""


class ClipStashHandler(BaseHTTPRequestHandler):
    server_version = "clipstashd/" + __version__
    root: str | None = None
    config_path: str | None = None
    photoshop_auto: bool = False
    pending_history: list[dict[str, Any]] = []
    pending_history_lock: threading.Lock = threading.Lock()
    body: bytes = b""

    # (method, path regex for re.fullmatch, handler name); groups are the handler's arguments.
    ROUTES = (
        ("GET", r"/health", "_serve_health"),
        ("GET", r"/config", "_serve_config"),
        ("GET", r"/packets", "_serve_packets"),
        ("GET", r"/export\.csv", "_serve_export"),
        ("GET", r"/history/pending", "_serve_pending_history"),
        ("GET", r"/picker/([^/]+)", "_serve_picker"),
        ("GET", r"/picker/([^/]+)/frame/(\d+)", "_serve_frame"),
        ("POST", r"/packets", "_create_packet"),
        ("POST", r"/bursts", "_create_burst_session"),
        ("POST", r"/bursts/ffmpeg", "_create_ffmpeg_burst_session"),
        ("POST", r"/packets/([^/]+)/place-photoshop", "_place_packet_in_photoshop"),
        ("POST", r"/picker/([^/]+)/choose", "_choose_burst_frame"),
        ("PUT", r"/config", "_update_config"),
    )

    # Per method, first match wins; the body is {"ok": false, "error": str(exc)}.
    # POST and PUT answer any other error with "internal error: ..." 500; GET
    # has no last-resort guard, so other GET errors still drop the connection.
    ERROR_STATUS = {
        "GET": ((FileNotFoundError, 404),),
        "POST": (
            (_UnsupportedContentType, 415),
            (ValueError, 400),
            (FileNotFoundError, 404),
            (FFmpegBurstError, 502),
        ),
        "PUT": (
            # An unusable config.json (a ValueError) is not the request's fault.
            (ConfigError, 500),
            (ValueError, 400),
            (OSError, 400),
        ),
    }

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

    def _json_body(self) -> Any:
        return json.loads(self.body.decode("utf-8") or "{}")

    def _read_payload(self, *, multipart: bool) -> tuple[Any, dict[str, bytes] | None]:
        """Decode the body as JSON -> (payload, None), or, where the route takes
        it, multipart/form-data -> (fields, files). Anything else is a 415."""
        content_type = self.headers.get("Content-Type") or ""
        if multipart and content_type.startswith("multipart/form-data"):
            return _parse_multipart(content_type, self.body)
        if content_type.startswith(JSON):
            return self._json_body(), None
        raise _UnsupportedContentType("expected JSON or multipart/form-data")

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path
        if method != "GET":
            self.body = self._read_body()
        try:
            for route_method, pattern, handler in self.ROUTES:
                match = re.fullmatch(pattern, path) if route_method == method else None
                if match:
                    getattr(self, handler)(*match.groups())
                    return
            self._send_json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:  # governance: allow-silent SF002: not silent; every caught error is answered with its mapped status (or 500) and its message, and GET re-raises what it does not map
            for error_class, status in self.ERROR_STATUS[method]:
                if isinstance(exc, error_class):
                    self._send_json({"ok": False, "error": str(exc)}, status)
                    return
            if method == "GET":
                raise
            self._send_json({"ok": False, "error": f"internal error: {exc}"}, 500)

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
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802
        self._dispatch("PUT")

    # -- action handlers ---------------------------------------------------
    def _serve_health(self) -> None:
        self._send_json({"ok": True, "version": __version__})

    def _serve_packets(self) -> None:
        self._send_json({"ok": True, "packets": list_packets(self.root)})

    def _serve_export(self) -> None:
        self._send(200, export_csv(self.root).encode("utf-8"), "text/csv")

    def _config_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "packet_root": str(Path(self.root or default_packet_root()).expanduser().resolve()),
            "default_root": str(default_packet_root()),
            "config_path": str(self.config_path or default_config_path()),
        }

    def _serve_config(self) -> None:
        self._send_json(self._config_payload())

    def _update_config(self) -> None:
        payload = self._json_body()
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
        return as_bool(explicit) or self.photoshop_auto or env_photoshop_enabled()

    def _create_packet(self) -> None:
        payload, files = self._read_payload(multipart=True)
        if files is None:
            record, image, photoshop = _record_from_payload(payload)
        else:
            record, image, photoshop = _record_from_multipart(payload, files)
        written = write_packet(record, image, root=self.root)
        response: dict[str, Any] = {"ok": True, "packet": written}
        if self._photoshop_requested(photoshop):
            response["photoshop"] = place_in_photoshop(
                packet_image_path(self.root, written)
            )
        self._send_json(response, 201)

    def _create_burst_session(self) -> None:
        payload, _ = self._read_payload(multipart=False)
        frames, metadata = _burst_from_payload(payload)
        self._send_burst_created(create_burst(frames, metadata))

    def _create_ffmpeg_burst_session(self) -> None:
        """Create a native multi-frame burst from a media URL (JSON only).

        The extension calls this when its in-page canvas burst is tainted and
        the video element exposes a media URL. Requires ffmpeg on PATH;
        otherwise responds 503 so the extension falls back to its single
        visible-tab still (``burst_visible_tab``).
        """
        payload = self._json_body()
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        media_url = str(payload.get("media_url") or "").strip()
        if not media_url:
            raise ValueError("media_url is required")
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            self._send_json({"ok": False, "error": FFMPEG_MISSING}, 503)
            return
        frames = burst_frames_from_url(
            media_url,
            payload.get("timestamp_sec"),
            site=str(payload.get("site") or "generic"),
            page_url=payload.get("page_url") or None,
            ffmpeg=ffmpeg,
        )
        metadata = packet_fields(payload, strict_timestamp=True)
        metadata["capture_method"] = "burst_ffmpeg"
        meta = create_burst(frames, metadata)
        self._send_burst_created(meta, capture_method="burst_ffmpeg")

    def _send_burst_created(self, meta: dict[str, Any], **extra: Any) -> None:
        session_id = str(meta["session_id"])
        self._send_json(
            {
                "ok": True,
                "session_id": session_id,
                "picker_url": self._picker_url(session_id),
                "frame_count": int(meta.get("frame_count") or 0),
                **extra,
            },
            201,
        )

    def _serve_picker(self, session_id: str) -> None:
        purge_expired_bursts()
        meta = load_session(session_id)
        html = _picker_html(session_id, meta).encode("utf-8")
        self._send(200, html, "text/html; charset=utf-8")

    def _serve_frame(self, session_id: str, index: str) -> None:
        meta = load_session(session_id)
        frame_count = int(meta.get("frame_count") or 0)
        frame_index = int(index)
        if frame_index < 0 or frame_index >= frame_count:
            self._send_json({"ok": False, "error": "frame not found"}, 404)
            return
        path = session_dir(session_id) / frame_filename(frame_index)
        if not path.exists():
            self._send_json({"ok": False, "error": "frame not found"}, 404)
            return
        self._send(200, path.read_bytes(), "image/png")

    def _choose_burst_frame(self, session_id: str) -> None:
        payload = self._json_body()
        try:
            frame_index = int(payload.get("frame_index"))
        except (TypeError, ValueError) as exc:
            raise ValueError("frame_index is required") from exc
        meta = load_session(session_id)
        photoshop = self._photoshop_requested(
            as_bool(payload.get("photoshop")) or as_bool(meta.get("photoshop"))
        )
        record = choose_frame(session_id, frame_index, payload, root=self.root)
        self._enqueue_pending_history(_history_entry_from_record(record))
        response: dict[str, Any] = {"ok": True, "packet": record}
        if photoshop:
            response["photoshop"] = place_in_photoshop(
                packet_image_path(self.root, record)
            )
        self._send_json(response, 200)

    def _place_packet_in_photoshop(self, packet_id: str) -> None:
        record = read_record(packet_id, root=self.root)
        image = packet_image_path(self.root, record)
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
    # A given root is returned as-is without reading config, so cli.main's already-resolved root costs no second read.
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
    """Serve until interrupted; create_server resolves ``root`` (cli.main passes it resolved)."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("clipstashd refuses to bind to non-loopback interfaces")
    server = create_server(
        host=host,
        port=port,
        root=root,
        photoshop_auto=photoshop_auto,
        config_path=config_path,
    )
    actual = server.server_address[1]
    root_shown = server.RequestHandlerClass.root
    print(f"clipstashd {__version__} listening on http://{host}:{actual} (root: {root_shown})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nclipstashd stopped")
    finally:
        server.server_close()
