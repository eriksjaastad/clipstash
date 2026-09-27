"""Packet schema and on-disk storage.

A packet is the source of truth for one capture:

    <root>/<site>/<id>/
      <slug>.png
      record.yaml

`record.yaml` follows PLAN.md §2. The still file is named from the slugified
video title (or the popup type-in override) via `helper.slug`; the record
carries that choice in ``image`` (``<slug>.png``) and ``name`` (the slug).
Packets are grouped by site folder whose name matches the adapter ids:
``youtube``, ``tiktok``, ``instagram``, ``x``, or ``generic``.

Packets written before site folders shipped live in a legacy flat layout:

    <root>/<id>/
      still.png
      record.yaml

Reads (`read_record`, `list_packets`, `packet_image_path`) tolerate that
legacy layout so remakes, CSV export and place-by-id keep working. New writes
always use the site layout and a slug; ``IMAGE_FILENAME`` remains only as the
legacy read default.

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

from .slug import default_slug, slugify, still_filename

IMAGE_FILENAME = "still.png"
RECORD_FILENAME = "record.yaml"

#: Adapter ids; these are the only allowed site folder names under the root.
SITE_IDS = ("youtube", "tiktok", "instagram", "x", "generic")

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


def image_path(directory: Path) -> Path:
    """Legacy still path for a flat packet directory (``still.png``)."""
    return directory / IMAGE_FILENAME


def _slug_for(
    title: str,
    site: str,
    packet_id: str,
    name: str | None = None,
) -> str:
    """Pick the still slug: type-in override > title > ``<site>-<id[:8]>``."""
    candidate = str(name or "").strip() or title
    return slugify(candidate, fallback="") or default_slug(title, site, packet_id)


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
    """Build a packet record dict from PLAN.md §2.

    `capture_method` records how the still was produced. Single captures use
    `canvas` (in-page video frame draw) or `visible_tab`
    (chrome.tabs.captureVisibleTab fallback for tainted canvases). Burst
    sessions use `burst_canvas` (in-page canvas stepping), `burst_ffmpeg`
    (helper-native multi-frame extraction from the video's media URL via
    ffmpeg), or `burst_visible_tab` (single visible-tab fallback when the
    canvas is tainted and the media cannot be fetched / ffmpeg is missing).
    A `visible_tab` still may be cropped to
    the video element's on-screen rectangle before it is written when the
    request carries a `crop_rect` (see helper.crop); the field itself is
    ephemeral and never persisted here.

    `name` is the optional popup type-in override for the still file name.
    It is slugified here (and re-slugified in `write_packet`); when it is
    empty the slugified title is used, and when that is empty or unusable the
    fallback is ``<site>-<id[:8]>`` so the still is never a forever-``still.png``.
    """
    if not title or not title.strip():
        raise ValueError("title is required")
    if not source_url or not source_url.strip():
        raise ValueError("source_url is required")
    title = title.strip()
    source_url = source_url.strip()
    page_url = (page_url or source_url).strip()

    packet_id = packet_id or new_packet_id()
    site = normalize_site(site)
    slug = _slug_for(title, site, packet_id, name)
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
    record.setdefault("image", IMAGE_FILENAME)
    record.setdefault("clipboard", {})


def write_packet(
    record: dict[str, Any],
    image_bytes: bytes,
    root: str | Path | None = None,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    """Persist a packet: ``<root>/<site>/<id>/{<slug>.png,record.yaml}``.

    `name` is an optional type-in override from the client; it is always
    re-slugified here, so the server never trusts a client-supplied file
    name. Without an override the record's existing ``name`` (or title) is
    slugified again. Returns the record as written (record.yaml is the source
    of truth).
    """
    _validate_record(record)
    packet_id = str(record["id"])
    site = normalize_site(record.get("site"))
    record["site"] = site
    title = str(record.get("title") or "")
    if name is None:
        name = str(record.get("name") or "") or None
    slug = _slug_for(title, site, packet_id, name)
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
    """Locate a packet directory in the site layout or the legacy flat layout."""
    base = resolve_root(root)
    candidates = [base / site / packet_id for site in SITE_IDS]
    flat = base / packet_id
    if flat not in candidates:
        candidates.append(flat)
    if base.exists():
        known = set(candidates)
        for child in base.iterdir():
            if child.is_dir() and child not in known:
                candidates.append(child / packet_id)
    for directory in candidates:
        if (directory / RECORD_FILENAME).exists():
            return directory
    raise FileNotFoundError(f"no packet with id {packet_id!r} under {base}")


def read_record(packet_id: str, root: str | Path | None = None) -> dict[str, Any]:
    path = record_path(find_packet_dir(packet_id, root))
    with path.open("r", encoding="utf-8") as fh:
        record = yaml.safe_load(fh) or {}
    record.setdefault("id", packet_id)
    record.setdefault("site", "generic")
    return record


def packet_image_path(
    root: str | Path | None,
    record: dict[str, Any],
) -> Path:
    """Absolute path to a packet's still image from its record.

    Resolves the packet directory the same way `read_record` does (site
    layout first, legacy flat tolerated), then applies ``record["image"]``.
    """
    directory = find_packet_dir(str(record["id"]), root)
    return directory / str(record.get("image") or IMAGE_FILENAME)


def summarize(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "created_at": record.get("created_at"),
        "title": record.get("title"),
        "source_url": record.get("source_url"),
        "page_url": record.get("page_url"),
        "timestamp_sec": record.get("timestamp_sec"),
        "site": record.get("site"),
        "capture_method": record.get("capture_method"),
        "name": record.get("name"),
        "image": record.get("image"),
    }


def list_packets(root: str | Path | None = None) -> list[dict[str, Any]]:
    """List packet summaries, newest first.

    Walks one level of site folders (``<root>/<site>/<id>/``) and also picks
    up legacy flat packets (``<root>/<id>/``) so old captures stay visible.
    """
    base = resolve_root(root)
    summaries: list[dict[str, Any]] = []
    if not base.exists():
        return summaries

    directories: list[Path] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if (child / RECORD_FILENAME).exists():
            # Legacy flat packet directory.
            directories.append(child)
            continue
        for directory in child.iterdir():
            if directory.is_dir() and (directory / RECORD_FILENAME).exists():
                directories.append(directory)

    for directory in directories:
        path = record_path(directory)
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
        "capture_method",
        "name",
        "image",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for summary in list_packets(root):
        writer.writerow([summary.get(column, "") for column in columns])
    return output.getvalue()
