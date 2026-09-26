"""Burst sessions: temporary frame stores behind the picker UI.

A burst session is created by ``POST /bursts`` (one PNG per frame) and lives
under the system temp dir:

    <tmp>/clipstash-bursts/<session_id>/
      meta.json
      frame_000.png
      frame_001.png
      ...

Choosing a frame writes a normal packet through :func:`helper.packets.write_packet`
and deletes the session. Sessions also expire after ``SESSION_TTL_SECONDS``.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .packets import new_packet_id, new_record, now_iso, write_packet

SESSION_TTL_SECONDS = 45 * 60  # 45 minutes, within the 30-60 min brief window
META_FILENAME = "meta.json"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def bursts_root() -> Path:
    """Directory holding all burst sessions (system temp, never packet root)."""
    return Path(tempfile.gettempdir()) / "clipstash-bursts"


def session_dir(session_id: str) -> Path:
    return bursts_root() / session_id


def frame_filename(index: int) -> str:
    return f"frame_{index:03d}.png"


def _is_expired(created_at: str) -> bool:
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age > SESSION_TTL_SECONDS


def purge_expired_bursts() -> int:
    """Delete expired sessions; returns how many were removed."""
    base = bursts_root()
    if not base.exists():
        return 0
    removed = 0
    for directory in base.iterdir():
        if not directory.is_dir():
            continue
        meta_path = directory / META_FILENAME
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            created_at = meta.get("created_at") or ""
            if created_at and _is_expired(created_at):
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
        except (OSError, ValueError):
            continue
    return removed


def create_burst(
    frames: list[bytes],
    metadata: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Persist a burst session and return its metadata (no picker URL here)."""
    if not frames:
        raise ValueError("burst requires at least one frame")
    for index, frame in enumerate(frames):
        if not frame:
            raise ValueError(f"frame {index} is empty")
        if not frame.startswith(PNG_MAGIC):
            raise ValueError(f"frame {index} is not a PNG image")

    purge_expired_bursts()

    session_id = session_id or new_packet_id()
    directory = session_dir(session_id)
    directory.mkdir(parents=True, exist_ok=False)

    meta: dict[str, Any] = {
        "session_id": session_id,
        "created_at": now_iso(),
        "frame_count": len(frames),
        "title": str(metadata.get("title") or ""),
        "source_url": str(metadata.get("source_url") or ""),
        "page_url": str(metadata.get("page_url") or metadata.get("source_url") or ""),
        "site": str(metadata.get("site") or "generic"),
        "timestamp_sec": _optional_float(metadata.get("timestamp_sec")),
        "capture_method": str(metadata.get("capture_method") or "burst_canvas"),
        "photoshop": _as_bool(metadata.get("photoshop")),
    }
    (directory / META_FILENAME).write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    for index, frame in enumerate(frames):
        (directory / frame_filename(index)).write_bytes(frame)
    return meta


def load_session(session_id: str) -> dict[str, Any]:
    """Load session metadata, deleting the session if it has expired."""
    directory = session_dir(session_id)
    meta_path = directory / META_FILENAME
    if not meta_path.exists():
        raise FileNotFoundError(f"no burst session with id {session_id!r}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FileNotFoundError(f"burst session {session_id!r} is unreadable")
    created_at = meta.get("created_at") or ""
    if created_at and _is_expired(created_at):
        shutil.rmtree(directory, ignore_errors=True)
        raise FileNotFoundError(f"burst session {session_id!r} expired")
    return meta


def choose_frame(
    session_id: str,
    frame_index: int,
    metadata: dict[str, Any],
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Write the chosen frame as a normal packet and delete the session."""
    meta = load_session(session_id)
    directory = session_dir(session_id)
    frame_count = int(meta.get("frame_count") or 0)
    if frame_index < 0 or frame_index >= frame_count:
        raise ValueError(f"frame_index must be between 0 and {frame_count - 1}")
    frame_path = directory / frame_filename(frame_index)
    if not frame_path.exists():
        raise FileNotFoundError(f"frame {frame_index} is missing from burst session")

    title = str(metadata.get("title") or meta.get("title") or "")
    source_url = str(metadata.get("source_url") or meta.get("source_url") or "")
    record = new_record(
        title=title,
        source_url=source_url,
        page_url=str(metadata.get("page_url") or meta.get("page_url") or source_url),
        site=str(metadata.get("site") or meta.get("site") or "generic"),
        timestamp_sec=_optional_float(
            metadata.get("timestamp_sec", meta.get("timestamp_sec"))
        ),
        capture_method=str(
            metadata.get("capture_method")
            or meta.get("capture_method")
            or "burst_canvas"
        ),
    )
    written = write_packet(record, frame_path.read_bytes(), root=root)
    shutil.rmtree(directory, ignore_errors=True)
    return written


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    """Accept JSON booleans and common string forms of truthiness."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")
