"""Packet schema and on-disk storage.

A packet is the source of truth for one capture:

    <root>/<id>/
      still.png
      record.yaml

`record.yaml` follows PLAN.md §2. The default root is
`~/Clipstash/packets` and can be overridden with the `CLIPSTASH_ROOT`
environment variable or the `--root` CLI flag.
"""

from __future__ import annotations

import csv
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

IMAGE_FILENAME = "still.png"
RECORD_FILENAME = "record.yaml"

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """Generate a 26-char ULID string (Crockford base32, no deps)."""
    timestamp = int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    value = (timestamp << 80) | randomness
    chars: list[str] = []
    for shift in range(25, -1, -1):
        chars.append(_CROCKFORD[(value >> (shift * 5)) & 0x1F])
    return "".join(chars)


def new_packet_id() -> str:
    return ulid()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_root() -> Path:
    """Default packet root: ~/Clipstash/packets (or $CLIPSTASH_ROOT)."""
    env = os.environ.get("CLIPSTASH_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Clipstash" / "packets"


def resolve_root(root: str | Path | None = None) -> Path:
    if root:
        return Path(root).expanduser()
    return default_root()


def packet_dir(root: str | Path | None, packet_id: str) -> Path:
    return resolve_root(root) / packet_id


def record_path(directory: Path) -> Path:
    return directory / RECORD_FILENAME


def image_path(directory: Path) -> Path:
    return directory / IMAGE_FILENAME


def new_record(
    title: str,
    source_url: str,
    page_url: str | None = None,
    site: str = "generic",
    timestamp_sec: float | None = None,
    tags: list[str] | None = None,
    notes: str = "",
    packet_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a packet record dict from PLAN.md §2."""
    if not title or not title.strip():
        raise ValueError("title is required")
    if not source_url or not source_url.strip():
        raise ValueError("source_url is required")
    title = title.strip()
    source_url = source_url.strip()
    page_url = (page_url or source_url).strip()

    return {
        "id": packet_id or new_packet_id(),
        "created_at": created_at or now_iso(),
        "title": title,
        "source_url": source_url,
        "page_url": page_url,
        "timestamp_sec": timestamp_sec,
        "site": site or "generic",
        "image": IMAGE_FILENAME,
        "tags": list(tags or []),
        "notes": notes or "",
        "clipboard": {
            "title": title,
            "url": source_url,
            "text": f"{title}\n{source_url}",
        },
    }


def _validate_record(record: dict[str, Any]) -> None:
    for field in ("id", "title", "source_url"):
        if not record.get(field):
            raise ValueError(f"record.{field} is required")
    if record.get("page_url"):
        record["page_url"] = record["page_url"].strip()
    record.setdefault("site", "generic")
    record.setdefault("tags", [])
    record.setdefault("notes", "")
    record.setdefault("image", IMAGE_FILENAME)
    record.setdefault("clipboard", {})


def write_packet(
    record: dict[str, Any],
    image_bytes: bytes,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Persist a packet: <root>/<id>/{still.png,record.yaml}.

    Returns the record as written (record.yaml is the source of truth).
    """
    _validate_record(record)
    directory = packet_dir(root, str(record["id"]))
    directory.mkdir(parents=True, exist_ok=True)
    image_path(directory).write_bytes(image_bytes)
    record["image"] = IMAGE_FILENAME
    data = yaml.safe_dump(record, sort_keys=False, allow_unicode=True)
    record_path(directory).write_text(data, encoding="utf-8")
    return record


def read_record(packet_id: str, root: str | Path | None = None) -> dict[str, Any]:
    path = record_path(packet_dir(root, packet_id))
    if not path.exists():
        raise FileNotFoundError(f"no packet with id {packet_id!r} under {resolve_root(root)}")
    with path.open("r", encoding="utf-8") as fh:
        record = yaml.safe_load(fh) or {}
    record.setdefault("id", packet_id)
    return record


def summarize(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "created_at": record.get("created_at"),
        "title": record.get("title"),
        "source_url": record.get("source_url"),
        "page_url": record.get("page_url"),
        "timestamp_sec": record.get("timestamp_sec"),
        "site": record.get("site"),
        "image": record.get("image"),
    }


def list_packets(root: str | Path | None = None) -> list[dict[str, Any]]:
    """List packet summaries, newest first."""
    base = resolve_root(root)
    summaries: list[dict[str, Any]] = []
    if not base.exists():
        return summaries
    for directory in base.iterdir():
        if not directory.is_dir():
            continue
        path = record_path(directory)
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                record = yaml.safe_load(fh) or {}
            record.setdefault("id", directory.name)
            summaries.append(summarize(record))
        except (OSError, yaml.YAMLError):
            continue
    summaries.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return summaries


def export_csv(root: str | Path | None = None) -> str:
    """CSV of packets (derived export, never the source of truth)."""
    columns = [
        "id",
        "created_at",
        "title",
        "source_url",
        "page_url",
        "timestamp_sec",
        "site",
        "image",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for summary in list_packets(root):
        writer.writerow([summary.get(column, "") for column in columns])
    return output.getvalue()
