"""Drift-guard for the ``helper.photoshop`` module docstring.

The module docstring is the single source of truth for the Photoshop place
mode's failure vocabulary. These tests pin it to the module it documents:
every reason token that appears in the docstring must be emitted by the
code, and every reason code the code can emit must be documented in the
module docstring's Troubleshooting section.
"""

from __future__ import annotations

import re
from pathlib import Path

from helper import photoshop

# Reason codes that do not share the ``photoshop_`` prefix but are still part
# of the public failure vocabulary. The docstring must document them; this is
# the doc-side vocabulary, not an allowlist that hides codes from the
# emitted-side check below.
_NON_PHOTOSHOP_REASON_CODES = frozenset(
    {
        "unsupported_platform",
        "missing_image",
        "osascript_missing",
    }
)

#: ``"reason": "..."`` return-dict literals and ``reason = "..."`` assignments.
_REASON_LITERAL_RES = (
    re.compile(r'"reason"\s*:\s*"([a-z][a-z_]+)"'),
    re.compile(r'\breason\s*=\s*"([a-z][a-z_]+)"'),
)

_DOC_PHOTOSHOP_REASON_RE = re.compile(r"\bphotoshop_[a-z_]+")


def _source_text() -> str:
    source = Path(photoshop.__file__).read_text(encoding="utf-8")
    return source


def _emitted_reason_codes() -> set[str]:
    source = _source_text()
    codes: set[str] = set()
    for pattern in _REASON_LITERAL_RES:
        codes.update(pattern.findall(source))
    return codes


def _documented_reason_tokens(doc: str) -> set[str]:
    tokens = set(_DOC_PHOTOSHOP_REASON_RE.findall(doc))
    tokens.update(code for code in _NON_PHOTOSHOP_REASON_CODES if code in doc)
    return tokens


def test_every_documented_reason_token_is_emitted_by_code() -> None:
    emitted = _emitted_reason_codes()
    documented = _documented_reason_tokens(photoshop.__doc__ or "")
    assert documented, "expected reason-code tokens in helper.photoshop.__doc__"
    for token in sorted(documented):
        assert token in emitted, (
            f"{token} appears in helper.photoshop.__doc__ but is not emitted by the code"
        )


def test_every_emitted_reason_code_is_documented() -> None:
    emitted = _emitted_reason_codes()
    assert emitted, "expected reason codes emitted by helper.photoshop"
    for code in sorted(emitted):
        assert code in photoshop.__doc__, (
            f"{code} is emitted by helper.photoshop but missing from its docstring"
        )
