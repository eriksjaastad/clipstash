"""Optional Photoshop place mode (macOS-only).

After a packet is written, the still can be handed to Photoshop over Apple
Events. This module generates and runs small AppleScripts through ``osascript``
and never imports or requires Photoshop — every failure is converted into a
clear JSON-shaped dict so callers (and the HTTP layer) never crash.

Version note: modern Photoshop releases (2025/2026) removed the ``place``
AppleEvent from their scripting dictionary, so this module tries three scripts
in order and falls back gracefully:

  1. ``place``  — legacy Photoshop (CS/CC through ~2021): places the PNG file
                  directly into the frontmost document.
  2. ``duplicate`` — modern Photoshop: opens the PNG as a document, duplicates
                  its art layers into the frontmost document, closes the temp
                  document. This keeps the "drop into open PS doc" behaviour.
  3. ``open``   — last resort: open the PNG as a new document in Photoshop.

Scripts receive the image path via ``on run argv`` (never string-interpolated),
so paths with quotes/spaces are safe.

Configuration
-------------
``CLIPSTASH_PHOTOSHOP=1`` (or the ``--photoshop`` serve flag) tells the helper
to place every saved packet automatically. Otherwise callers opt in per request
with ``photoshop: true`` or use ``POST /packets/{id}/place-photoshop``.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

PHOTOSHOP_BUNDLE_ID = "com.adobe.Photoshop"

MARKER_PLACED = "CLIPSTASH_PLACED"
MARKER_OPENED = "CLIPSTASH_OPENED"
MARKER_NOT_RUNNING = "CLIPSTASH_NOT_RUNNING"
MARKER_NO_DOCUMENT = "CLIPSTASH_NO_DOCUMENT"
MARKER_ERROR = "CLIPSTASH_ERROR:"
MARKER_NO_ARG = "CLIPSTASH_NO_ARG"

# Tuple of (name, script_text). Order matters: the first script that compiles
# against the installed Photoshop dictionary and produces a definitive result
# wins; compile failures move on to the next script.
_PLACEMENT_SCRIPTS: tuple[tuple[str, str], ...] = (
    (
        "place",
        f"""on run argv
\tif (count of argv) < 1 then
\t\treturn "{MARKER_NO_ARG}"
\tend if
\tset imagePath to item 1 of argv
\tset imageFile to POSIX file imagePath
\tif application id "{PHOTOSHOP_BUNDLE_ID}" is running then
\t\ttell application id "{PHOTOSHOP_BUNDLE_ID}"
\t\t\ttry
\t\t\t\tset docRef to current document
\t\t\ton error errMsg
\t\t\t\treturn "{MARKER_NO_DOCUMENT}: " & errMsg
\t\t\tend try
\t\t\ttry
\t\t\t\tplace docRef file imageFile
\t\t\ton error errMsg
\t\t\t\treturn "{MARKER_ERROR} " & errMsg
\t\t\tend try
\t\t\treturn "{MARKER_PLACED}"
\t\tend tell
\telse
\t\treturn "{MARKER_NOT_RUNNING}"
\tend if
end run""",
    ),
    (
        "duplicate",
        f"""on run argv
\tif (count of argv) < 1 then
\t\treturn "{MARKER_NO_ARG}"
\tend if
\tset imagePath to item 1 of argv
\tset imageFile to POSIX file imagePath
\tif application id "{PHOTOSHOP_BUNDLE_ID}" is running then
\t\ttell application id "{PHOTOSHOP_BUNDLE_ID}"
\t\t\ttry
\t\t\t\tset targetDoc to current document
\t\t\ton error errMsg
\t\t\t\treturn "{MARKER_NO_DOCUMENT}: " & errMsg
\t\t\tend try
\t\t\ttry
\t\t\t\tset openedDoc to open imageFile showing dialogs never
\t\t\t\tduplicate every art layer of openedDoc to targetDoc
\t\t\t\tclose openedDoc saving no
\t\t\ton error errMsg
\t\t\t\treturn "{MARKER_ERROR} " & errMsg
\t\t\tend try
\t\t\treturn "{MARKER_PLACED}"
\t\tend tell
\telse
\t\treturn "{MARKER_NOT_RUNNING}"
\tend if
end run""",
    ),
    (
        "open",
        f"""on run argv
\tif (count of argv) < 1 then
\t\treturn "{MARKER_NO_ARG}"
\tend if
\tset imagePath to item 1 of argv
\tset imageFile to POSIX file imagePath
\tif application id "{PHOTOSHOP_BUNDLE_ID}" is running then
\t\ttell application id "{PHOTOSHOP_BUNDLE_ID}"
\t\t\ttry
\t\t\t\topen imageFile showing dialogs never
\t\t\ton error errMsg
\t\t\t\treturn "{MARKER_ERROR} " & errMsg
\t\t\tend try
\t\t\treturn "{MARKER_OPENED}"
\t\tend tell
\telse
\t\treturn "{MARKER_NOT_RUNNING}"
\tend if
end run""",
    ),
)


def is_macos() -> bool:
    return sys.platform == "darwin"


def env_photoshop_enabled() -> bool:
    """True when CLIPSTASH_PHOTOSHOP requests auto-place (1/true/yes/on)."""
    return os.environ.get("CLIPSTASH_PHOTOSHOP", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def build_place_script() -> str:
    """AppleScript using the legacy ``place`` verb (older Photoshop)."""
    return _script_by_name("place")


def build_duplicate_script() -> str:
    """AppleScript that opens the PNG and duplicates its layers into the active doc."""
    return _script_by_name("duplicate")


def build_open_script() -> str:
    """AppleScript fallback that simply opens the PNG as a new document."""
    return _script_by_name("open")


def _script_by_name(name: str) -> str:
    for script_name, script in _PLACEMENT_SCRIPTS:
        if script_name == name:
            return script
    raise KeyError(name)


def scripts_for_placement() -> list[dict[str, str]]:
    """All candidate scripts in fallback order (useful for dry-run output)."""
    return [{"name": name, "script": script} for name, script in _PLACEMENT_SCRIPTS]


def _command_for(script: str, image_path: Path) -> list[str]:
    """osascript invocation for a script; image path travels as an argv item."""
    return ["osascript", "-e", script, str(image_path)]


def _is_compile_failure(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "syntax error" in lowered
        or "expected " in lowered
        or "-2740" in lowered
        or "-2741" in lowered
    )


def _looks_like_not_installed(stderr: str) -> bool:
    return "-1728" in stderr or "can’t get application id" in stderr.lower()


def _looks_like_automation_denied(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "-1743" in stderr
        or "not authorized" in lowered
        or "not allowed" in lowered
        or "not permitted" in lowered
    )


def _looks_like_timeout(message: str) -> bool:
    return "timed out" in message.lower() or "-1712" in message


def _classify_script_result(
    script_name: str,
    completed: subprocess.CompletedProcess[str],
    image_path: Path,
) -> dict[str, Any] | None:
    """Turn one osascript run into a result dict, or None to try the next script."""
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    if completed.returncode == 0:
        if MARKER_PLACED in stdout:
            return {"ok": True, "placed": True, "image": str(image_path), "method": script_name}
        if MARKER_OPENED in stdout:
            return {
                "ok": True,
                "placed": True,
                "image": str(image_path),
                "method": script_name,
                "note": "opened as a new document (Photoshop version has no place/duplicate path)",
            }
        if MARKER_NOT_RUNNING in stdout:
            return {
                "ok": False,
                "error": "Photoshop is not running",
                "reason": "photoshop_not_running",
                "image": str(image_path),
            }
        if MARKER_NO_DOCUMENT in stdout:
            detail = stdout.split(":", 1)[1].strip() if ":" in stdout else ""
            reason = "photoshop_no_document"
            if _looks_like_timeout(detail):
                reason = "photoshop_timeout"
            return {
                "ok": False,
                "error": f"Photoshop is running but has no open document{f': {detail}' if detail else ''}",
                "reason": reason,
                "image": str(image_path),
            }
        if stdout.startswith(MARKER_ERROR):
            detail = stdout[len(MARKER_ERROR):].strip()
            return _error_result(detail, image_path, script_name)
        # No marker and exit 0: unexpected, treat as failure.
        return {
            "ok": False,
            "error": f"osascript returned no result marker ({stdout or 'empty output'})",
            "reason": "photoshop_unexpected_output",
            "image": str(image_path),
        }

    # Non-zero exit. Compile failures mean the verb is not in this Photoshop
    # version's dictionary — the caller moves on to the next script.
    if _is_compile_failure(stderr):
        return None
    if _looks_like_not_installed(stderr):
        return {
            "ok": False,
            "error": "Adobe Photoshop is not installed (com.adobe.Photoshop not found)",
            "reason": "photoshop_not_installed",
            "image": str(image_path),
        }
    return _error_result(stderr or f"osascript exited {completed.returncode}", image_path, script_name)


def _error_result(message: str, image_path: Path, script_name: str) -> dict[str, Any]:
    reason = "photoshop_osascript_failed"
    if _looks_like_automation_denied(message):
        reason = "photoshop_automation_denied"
    elif _looks_like_timeout(message):
        reason = "photoshop_timeout"
    elif "no document" in message.lower():
        reason = "photoshop_no_document"
    return {
        "ok": False,
        "error": f"Photoshop {script_name} failed: {message}",
        "reason": reason,
        "image": str(image_path),
    }


def place_in_photoshop(
    image_path: str | Path,
    *,
    timeout: float = 120.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Place a PNG into Photoshop's frontmost document (macOS only).

    Returns ``{"ok": True, "placed": True, ...}`` on success or a clear
    ``{"ok": False, "error": ..., "reason": ...}`` dict on failure. Never raises
    for Photoshop-side problems.
    """
    path = Path(image_path).expanduser()
    if not is_macos():
        return {
            "ok": False,
            "error": "Photoshop place mode is macOS-only",
            "reason": "unsupported_platform",
            "image": str(path),
        }
    if not path.is_absolute():
        path = path.resolve()

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "image": str(path),
            "scripts": scripts_for_placement(),
            "commands": [
                _command_for(script, path) for _name, script in _PLACEMENT_SCRIPTS
            ],
        }

    if not path.exists():
        return {
            "ok": False,
            "error": f"image not found: {path}",
            "reason": "missing_image",
            "image": str(path),
        }

    compile_failures: list[str] = []
    for script_name, script in _PLACEMENT_SCRIPTS:
        command = _command_for(script, path)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return {
                "ok": False,
                "error": "osascript not found (macOS-only feature)",
                "reason": "osascript_missing",
                "image": str(path),
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"osascript timed out after {timeout:g}s waiting for Photoshop",
                "reason": "photoshop_timeout",
                "image": str(path),
            }

        result = _classify_script_result(script_name, completed, path)
        if result is not None:
            return result
        compile_failures.append(f"{script_name}: {completed.stderr.strip()}")

    # Every script failed to compile against the installed Photoshop dictionary.
    return {
        "ok": False,
        "error": "no Photoshop AppleScript path available for this Photoshop version",
        "reason": "photoshop_unsupported",
        "image": str(path),
        "details": compile_failures,
    }


def quoted_command_for_script(script_name: str, image_path: str | Path) -> str:
    """Human-readable shell command for a single script (docs/tests)."""
    script = _script_by_name(script_name)
    return " ".join(shlex.quote(part) for part in _command_for(script, Path(image_path)))
