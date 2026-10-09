"""Request-field coercion shared by the HTTP server, burst sessions and the CLI."""

from __future__ import annotations

import os
from typing import Any


def as_bool(value: Any) -> bool:
    """Accept JSON booleans and common string forms of truthiness."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def env_flag(name: str) -> bool:
    """True when environment variable *name* is set to 1/true/yes/on."""
    # governance: allow-silent SF003: optional opt-in flag; unset/empty means off, which this returns as False
    return as_bool(os.environ.get(name))


def optional_float(value: Any, *, strict: bool = False) -> float | None:
    """Parse optional numeric metadata such as ``timestamp_sec``.

    None, "" and non-numeric text become None. A value float() rejects by
    type (a JSON list or object) becomes None too, unless *strict*: then
    the TypeError propagates.
    """
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:  # governance: allow-silent SF002: timestamp_sec is optional packet metadata; a non-numeric value is recorded as absent (None), as is an empty one
        if strict and isinstance(exc, TypeError):
            raise
        return None


def packet_fields(source: dict[str, Any], *, strict_timestamp: bool = False) -> dict[str, Any]:
    """The metadata a burst session carries into its packet, normalised.

    Every key is present: strings default to "", ``page_url`` falls back to
    ``source_url``, ``site`` to generic and ``capture_method`` to
    burst_canvas. A list or object ``timestamp_sec`` becomes None, unless
    *strict_timestamp*: then it raises TypeError (the server's burst routes
    have always answered that with their last-resort 500).
    """
    return {
        "title": str(source.get("title") or ""),
        "source_url": str(source.get("source_url") or ""),
        "page_url": str(source.get("page_url") or source.get("source_url") or ""),
        "site": str(source.get("site") or "generic"),
        "timestamp_sec": optional_float(source.get("timestamp_sec"), strict=strict_timestamp),
        "capture_method": str(source.get("capture_method") or "burst_canvas"),
        "photoshop": as_bool(source.get("photoshop")),
        "name": str(source.get("name") or ""),
    }
