"""Crop helper for tainted visible-tab captures.

When a cross-origin ``<video>`` taints the capture canvas, the extension falls
back to ``chrome.tabs.captureVisibleTab``, which returns the *whole* tab (side
bar, chrome, and all) rather than a clean video frame. To close that gap the
content script also reports a ``crop_rect`` — the page video element's
``getBoundingClientRect()`` box in CSS viewport coordinates plus
``window.devicePixelRatio`` — and this module crops the full-tab PNG to that
box before the packet/burst frame is written.

Coordinate model
----------------
``crop_rect``: ``{x, y, width, height, dpr}`` in CSS pixels. The captured PNG
is in device pixels, so the crop box is multiplied by ``dpr`` and rounded,
then clamped to the image bounds. Missing or invalid rects are treated as "no
crop" and the original bytes are returned unchanged (never a crash).

Caveat: letterboxed / object-fit videos may include black bars inside the
element box; the CSS box is the best practical crop without decoding the media
stream itself.
"""

from __future__ import annotations

import io
import math
from typing import Any

from PIL import Image


def apply_crop_rect(image_bytes: bytes, crop_rect: dict[str, Any] | None) -> bytes:
    """Crop PNG bytes to crop_rect in CSS pixels × dpr, clamped to image bounds.

    ``crop_rect``: ``{x, y, width, height, dpr}`` — CSS viewport coords from
    ``getBoundingClientRect()`` plus ``window.devicePixelRatio``. Missing or
    invalid rects, and rects that degenerate to zero size after clamping,
    return ``image_bytes`` unchanged.
    """
    if not isinstance(crop_rect, dict):
        return image_bytes

    dpr = _positive_float(crop_rect.get("dpr"), 1.0)
    try:
        x = float(crop_rect.get("x"))
        y = float(crop_rect.get("y"))
        width = float(crop_rect.get("width"))
        height = float(crop_rect.get("height"))
    except (TypeError, ValueError):
        return image_bytes
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        return image_bytes

    left = round(x * dpr)
    top = round(y * dpr)
    right = left + round(width * dpr)
    bottom = top + round(height * dpr)

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            left = max(0, min(left, image.width))
            top = max(0, min(top, image.height))
            right = max(0, min(right, image.width))
            bottom = max(0, min(bottom, image.height))
            if right <= left or bottom <= top:
                return image_bytes
            cropped = image.crop((left, top, right, bottom))
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:
        # Undecodable/truncated image bytes: keep the original, never crash the
        # request on a bad crop.
        return image_bytes


def _positive_float(value: Any, default: float) -> float:
    """Parse *value* as a positive finite float, else return *default*."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number <= 0:
        return default
    return number
