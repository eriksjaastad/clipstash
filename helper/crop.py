"""Crop helper for tainted visible-tab captures (see ``apply_crop_rect``)."""

from __future__ import annotations

import io
import math
from typing import Any

from PIL import Image


def apply_crop_rect(image_bytes: bytes, crop_rect: dict[str, Any] | None) -> bytes:
    """Crop PNG bytes to crop_rect in CSS pixels × dpr, clamped to image bounds.

    When a cross-origin ``<video>`` taints the capture canvas, the extension
    falls back to ``chrome.tabs.captureVisibleTab``, which returns the *whole*
    tab (side bar, chrome, and all). The content script then also reports
    ``crop_rect``: ``{x, y, width, height, dpr}``, the video element's
    ``getBoundingClientRect()`` box in CSS viewport coords plus
    ``window.devicePixelRatio``, and the full-tab PNG is cropped to it before
    the packet/burst frame is written. The PNG is in device pixels, so the
    box is multiplied by ``dpr`` and rounded, then clamped to the image.

    Missing or invalid rects, and rects that degenerate to zero size after
    clamping, return ``image_bytes`` unchanged (never a crash). Letterboxed /
    object-fit videos may keep black bars inside the element box; the CSS box
    is the best practical crop without decoding the media stream itself.
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
