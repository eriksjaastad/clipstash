"""Packet schema and on-disk storage.

A packet is the source of truth for one capture:

    <root>/<site>/<id>/
      <slug>.png
      record.yaml

``<site>`` is an adapter id from ``SITE_IDS``; anything else is stored under
``generic``. ``<slug>`` is the popup type-in name, else the video title, else
``<site>-<id[:8]>`` (``helper.slug.pick_slug``). ``record.yaml`` holds, in
this key order::

    id: 01J…                      # ULID
    created_at: '2026-09-25T…'    # UTC ISO-8601
    title: How I edit thumbnails
    source_url: https://www.youtube.com/watch?v=…
    page_url: https://www.youtube.com/watch?v=…   # defaults to source_url
    timestamp_sec: 142.5          # null when unknown
    site: youtube
    capture_method: canvas        # one of CAPTURE_METHODS
    image: how-i-edit-thumbnails.png
    name: how-i-edit-thumbnails   # the slug
    tags: []
    notes: ''
    clipboard:                    # what the extension copies
      title: How I edit thumbnails
      url: https://www.youtube.com/watch?v=…
      text: "How I edit thumbnails\\nhttps://www.youtube.com/watch?v=…"

``GET /packets`` and the CSV export carry only ``SUMMARY_FIELDS``.

Reads (`read_record`, `list_packets`, `packet_image_path`) only look in site
folders; the pre-site-folder flat layout (``<root>/<id>/still.png``) is no
longer read.

The default root is ``~/Clipstash/packets`` and can be overridden with the
``--root`` CLI flag, ``~/Clipstash/config.json`` (see ``helper.config``), or
the ``CLIPSTASH_ROOT`` environment variable.
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

from .slug import pick_slug, still_filename

RECORD_FILENAME = "record.yaml"

#: Adapter ids; these are the only allowed site folder names under the root.
SITE_IDS = ("youtube", "tiktok", "instagram", "x", "generic")

#: Known ``capture_method`` values (not validated): single captures, then bursts.
CAPTURE_METHODS = ("canvas", "visible_tab", "burst_canvas", "burst_ffmpeg", "burst_visible_tab")

#: Record fields in ``GET /packets`` summaries and CSV columns, in order.
SUMMARY_FIELDS = (
    "id", "created_at", "title", "source_url", "page_url",
    "timestamp_sec", "site", "capture_method", "name", "image",
)

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


def normalize_site(site: Any) -> str:
    """Return the adapter site id, or ``generic`` for anything unknown.

    Site names become folder names under the packet root, so only the known
    adapter ids pass through; anything else (empty, whitespace, or path-y
    values like ``../../``) collapses to ``generic``.
    """
    value = str(site or "").strip().lower()
    return value if value in SITE_IDS else "generic"


def packet_dir(
    root: str | Path | None,
    packet_id: str,
    site: str = "generic",
) -> Path:
    """Directory for a new-layout packet: ``<root>/<site>/<id>``."""
    return resolve_root(root) / normalize_site(site) / packet_id


def record_path(directory: Path) -> Path:
    return directory / RECORD_FILENAME


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
    capture_method: str = "canvas",
    name: str | None = None,
) -> dict[str, Any]:
    """Build a packet record dict (schema in the module docstring).

    `capture_method` is normally one of ``CAPTURE_METHODS``. `name` is the
    optional popup type-in for the still name; see ``helper.slug.pick_slug``.
    """
    if not title or not title.strip():
        raise ValueError("title is required")
    if not source_url or not source_url.strip():
        raise ValueError("source_url is required")
    title = title.strip()
    source_url = source_url.strip()
    page_url = (page_url or source_url).strip()

    packet_id = packet_id or ulid()
    site = normalize_site(site)
    slug = pick_slug(name, title, site, packet_id)
    return {
        "id": packet_id,
        "created_at": created_at or now_iso(),
        "title": title,
        "source_url": source_url,
        "page_url": page_url,
        "timestamp_sec": timestamp_sec,
        "site": site,
        "capture_method": capture_method or "canvas",
        "image": still_filename(slug),
        "name": slug,
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
    record.setdefault("capture_method", "canvas")
    record.setdefault("tags", [])
    record.setdefault("notes", "")
    record.setdefault("clipboard", {})


def write_packet(
    record: dict[str, Any],
    image_bytes: bytes,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Persist a packet: ``<root>/<site>/<id>/{<slug>.png,record.yaml}``.

    The record's ``name`` (or title) is always re-slugified here, so the
    server never trusts a client-supplied file name. Returns the record as
    written (record.yaml is the source of truth).
    """
    _validate_record(record)
    packet_id = str(record["id"])
    site = normalize_site(record.get("site"))
    record["site"] = site
    slug = pick_slug(record.get("name"), record.get("title"), site, packet_id)
    filename = still_filename(slug)
    record["name"] = slug
    record["image"] = filename

    directory = packet_dir(root, packet_id, site=site)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(image_bytes)
    data = yaml.safe_dump(record, sort_keys=False, allow_unicode=True)
    record_path(directory).write_text(data, encoding="utf-8")
    return record


def find_packet_dir(packet_id: str, root: str | Path | None = None) -> Path:
    """Locate ``<root>/<site>/<id>/``: adapter site folders first, then any other folder."""
    base = resolve_root(root)
    candidates = [base / site / packet_id for site in SITE_IDS]
    if base.exists():
        known = set(candidates)
        for child in base.iterdir():
            if child.is_dir() and child not in known:
                candidates.append(child / packet_id)
    for directory in candidates:
        if (directory / RECORD_FILENAME).exists():
            return directory
    raise FileNotFoundError(f"no packet with id {packet_id!r} under {base}")


def _load_record(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def read_record(packet_id: str, root: str | Path | None = None) -> dict[str, Any]:
    record = _load_record(record_path(find_packet_dir(packet_id, root)))
    record.setdefault("id", packet_id)
    record.setdefault("site", "generic")
    return record


def packet_image_path(
    root: str | Path | None,
    record: dict[str, Any],
) -> Path:
    """Absolute path to a packet's still image from its record.

    Resolves the packet directory the same way `read_record` does, then
    applies ``record["image"]``.
    """
    directory = find_packet_dir(str(record["id"]), root)
    if not record.get("image"):
        raise FileNotFoundError(f"packet {record['id']!r} has no still image")
    return directory / str(record["image"])


def summarize(record: dict[str, Any]) -> dict[str, Any]:
    # ``id`` is required (KeyError when missing); the other fields default to None.
    return {
        field: record[field] if field == "id" else record.get(field)
        for field in SUMMARY_FIELDS
    }


def list_packets(root: str | Path | None = None) -> list[dict[str, Any]]:
    """List packet summaries, newest first.

    Walks one level of site folders (``<root>/<site>/<id>/``).
    """
    base = resolve_root(root)
    summaries: list[dict[str, Any]] = []
    if not base.exists():
        return summaries

    directories: list[Path] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        for directory in child.iterdir():
            if directory.is_dir() and (directory / RECORD_FILENAME).exists():
                directories.append(directory)

    for directory in directories:
        try:
            record = _load_record(record_path(directory))
            record.setdefault("id", directory.name)
            summaries.append(summarize(record))
        except (OSError, yaml.YAMLError):
            continue
    summaries.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return summaries


def export_csv(root: str | Path | None = None) -> str:
    """CSV of packets (derived export, never the source of truth)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(SUMMARY_FIELDS)
    for summary in list_packets(root):
        writer.writerow([summary.get(column, "") for column in SUMMARY_FIELDS])
    return output.getvalue()
