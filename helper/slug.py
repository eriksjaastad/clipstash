"""Web-safe slug helpers for still file names and Photoshop layer names.

Stills are stored as ``<slug>.png`` under ``<root>/<site>/<id>/`` (see
``helper.packets``). The slug comes from the video title by default, or from
the optional type-in name in the extension popup, and must be safe for the
filesystem and for Photoshop layer/document names on every platform we touch:

* lowercase
* runs of non-alphanumeric characters become a single ``-``
* accents are folded to ASCII (``café`` -> ``cafe``); characters that cannot
  be folded are dropped
* leading/trailing dashes are stripped
* the result is capped at ``MAX_SLUG_LEN`` characters
* an empty result (empty input, punctuation-only, or a title in a script that
  folds to nothing) never escapes: ``slugify`` returns *fallback*, so the
  human-facing still is never a forever-``still.png`` file.

Public API
----------
``MAX_SLUG_LEN``
    Maximum slug length (80).
``slugify(text, *, fallback, max_length)``
    Normalize *text* into a web-safe slug; never returns an empty string.
``still_filename(slug)``
    Return ``"<slug>.png"``.
``default_slug(title, site, packet_id)``
    Title slug, or ``"<site>-<packet_id[:8]>"`` when the title is empty or
    useless.
"""

from __future__ import annotations

import re
import unicodedata

MAX_SLUG_LEN = 80

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify(
    text: str | None,
    *,
    fallback: str = "still",
    max_length: int = MAX_SLUG_LEN,
) -> str:
    """Normalize *text* into a lowercase, dash-separated, web-safe slug.

    ``café`` becomes ``cafe``, ``"How I edit — thumbnails!"`` becomes
    ``how-i-edit-thumbnails``. Characters that cannot be folded to ASCII
    (e.g. CJK titles) are dropped; if that empties the slug, *fallback* is
    returned. The result is capped at *max_length* characters and never
    ends in ``-``.
    """
    if text is None:
        text = ""
    folded = unicodedata.normalize("NFKD", str(text))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM_RE.sub("-", ascii_text.lower()).strip("-")
    if not slug:
        slug = fallback
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    if not slug:
        slug = fallback
    return slug


def still_filename(slug: str) -> str:
    """Return the still file name for *slug*: ``"<slug>.png"``."""
    return f"{slug}.png"


def default_slug(
    title: str | None,
    site: str = "generic",
    packet_id: str = "",
) -> str:
    """Default still slug: the title slug, else ``<site>-<packet_id[:8]>``.

    The fallback keeps the human-facing file name readable and unique-ish
    without ever falling back to a forever-``still.png``.
    """
    slug = slugify(title, fallback="")
    if slug:
        return slug
    short_id = str(packet_id or "")[:8]
    return slugify(f"{site}-{short_id}", fallback="still")
