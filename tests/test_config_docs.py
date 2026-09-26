"""Drift-guard for the ``helper.config`` module docstring.

The module docstring is the single source of truth for config behaviour, so
these tests pin it to the module it documents: the first line must summarise
the module, every name in its "Public API" section must be a real attribute
of ``helper.config``, and the docstring must mention the resolution pieces
by name rather than only in prose.
"""

from __future__ import annotations

import re

from helper import config

#: A documented public name opens a double-backtick span, e.g.
#: ``default_config_path()`` -> ``default_config_path``.
_NAME_RE = re.compile(r"``([A-Za-z_][A-Za-z0-9_]*)")

_UNDERLINE_CHARS = frozenset("-=~^")


def _doc_lines() -> list[str]:
    assert config.__doc__ is not None, "module docstring must exist"
    return config.__doc__.splitlines()


def _is_underline(line: str) -> bool:
    """True for a reST-style section underline (at least three same chars)."""
    stripped = line.strip()
    return (
        len(stripped) >= 3
        and stripped[0] in _UNDERLINE_CHARS
        and all(ch == stripped[0] for ch in stripped)
    )


def _section_lines(heading: str) -> list[str]:
    """Return the body lines of one reST section, excluding its header."""
    doc = _doc_lines()
    for i, line in enumerate(doc):
        if line.strip() == heading and i + 1 < len(doc) and _is_underline(doc[i + 1]):
            start = i + 2
            break
    else:
        return []

    lines: list[str] = []
    j = start
    while j < len(doc):
        # A non-empty line followed by an underline starts the next section.
        if j + 1 < len(doc) and doc[j].strip() and _is_underline(doc[j + 1]):
            break
        lines.append(doc[j])
        j += 1
    return lines


def _public_api_names() -> set[str]:
    names: set[str] = set()
    for line in _section_lines("Public API"):
        # Only entry lines (flush-left `` ``name`` ``) are API names; the
        # indented description lines may mention other identifiers.
        if line.startswith("``"):
            match = _NAME_RE.search(line)
            if match:
                names.add(match.group(1))
    return names


def test_module_docstring_first_line_is_non_empty() -> None:
    first_line = _doc_lines()[0].strip()
    assert first_line, "module docstring must have a non-empty first line"


def test_every_public_api_name_is_a_real_module_attribute() -> None:
    names = _public_api_names()
    assert names, "expected a non-empty 'Public API' section in the docstring"
    for name in sorted(names):
        assert hasattr(config, name), (
            f"{name} is listed under Public API but is not an attribute of helper.config"
        )


def test_docstring_documents_resolution_pieces_by_token() -> None:
    doc = "\n".join(_doc_lines())
    for token in ("--root", "CONFIG_FILENAME", "config.json", "CLIPSTASH_ROOT", "~/Clipstash/packets"):
        assert token in doc, f"helper.config docstring must mention {token!r}"
