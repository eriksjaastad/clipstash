"""Optional Photoshop place mode (macOS-only).

After a packet is written, the still can be handed to Photoshop over Apple
Events. This module generates and runs small AppleScripts through ``osascript``
and never imports or requires Photoshop — every failure is converted into a
clear JSON-shaped dict so callers (and the HTTP layer) never crash.
``place_in_photoshop()`` returns structured JSON-shaped dicts for every outcome
and never raises for Photoshop-side failures. macOS-only: no Windows support.

Enabling
--------
Any one of:

* Extension popup — tick "Also place in Photoshop" (default off; remembered in
  ``chrome.storage.local`` and applied to both Save packet and Burst & pick).
* Per request — send ``"photoshop": true`` in the ``POST /packets`` or
  ``POST /bursts`` JSON body.
* Helper config — ``CLIPSTASH_PHOTOSHOP=1`` before launch, or
  ``clipstashd serve --photoshop``, to place every saved packet automatically.

A saved packet can be re-placed manually with
``POST /packets/<id>/place-photoshop``. The packet save itself always succeeds:
when placement was requested, the outcome is returned in an extra ``photoshop``
field and never raises for Photoshop-side failures.

Placement scripts
-----------------
Modern Photoshop releases (2025/2026) removed the ``place`` AppleEvent from
their scripting dictionary, so this module tries three scripts in order and
falls back gracefully:

  1. ``place``     — legacy Photoshop (CS/CC through ~2021): places the PNG
                     file directly into the frontmost document.
  2. ``duplicate`` — modern Photoshop: opens the PNG as a document, duplicates
                     its art layers into the frontmost document, closes the
                     temp document. This keeps the "drop into open PS doc"
                     behaviour.
  3. ``open``      — last resort: opens the PNG as a new document in Photoshop
                     (reported as ``method: "open"`` — expected when the
                     installed version has no place/duplicate path).

Scripts receive the image path via ``on run argv`` (never string-interpolated),
so paths with quotes/spaces are safe. Each script is allowed up to 120 s; a
busy Photoshop reports a timeout instead of hanging.

The still arrives as ``<slug>.png`` under ``<root>/<site>/<id>/`` (see
``helper.packets``), so ``open``/``duplicate`` layers inherit the slug
basename automatically — no extra layer-naming step is needed here.

macOS permissions (TCC / Automation)
------------------------------------
The first time the helper tells Photoshop what to do, macOS asks whether the
launching app (e.g. Terminal, or the LaunchAgent's ``clipstashd``) may control
"Adobe Photoshop". Click OK — otherwise ``osascript`` fails with an
"not authorized" / ``-1743`` error and the helper reports
``photoshop_automation_denied``. Check or reset consent under System Settings →
Privacy & Security → Automation (delete the entry and trigger a place again to
re-prompt). For a LaunchAgent, grant Automation to the program named in the
plist (``clipstashd``), or start ``clipstashd`` from Terminal once to approve
Terminal.

Troubleshooting
---------------
Symptom → reason → fix (compact):

Photoshop isn't running
    → ``photoshop_not_running`` — launch Photoshop, open a document, retry.
Photoshop running but no open document
    → ``photoshop_no_document`` — open/create a document, retry.
``-1743`` / "not authorized"
    → ``photoshop_automation_denied`` — macOS Automation consent missing; see
      permissions above.
``AppleEvent timed out``
    → ``photoshop_timeout`` — Photoshop is busy (modal dialog, hung plug-in)
      or its scripting interface is slow; dismiss dialogs / restart, retry.
``-1728`` / Photoshop not found
    → ``photoshop_not_installed`` — no app with bundle id
      ``com.adobe.Photoshop``; install Photoshop (feature is optional).
All scripts fail to compile against this Photoshop version
    → ``photoshop_unsupported`` — non-blocking; see ISSUES.md.
``osascript`` exits 0 but prints no recognised marker
    → ``photoshop_unexpected_output`` — unexpected osascript output; report it.
``osascript`` fails for any other reason
    → ``photoshop_osascript_failed`` — generic osascript failure (not
      automation, timeout, or missing document).
Not macOS
    → ``unsupported_platform`` — feature is macOS-only; no Windows support.
Image file missing on disk
    → ``missing_image`` — the PNG was not written or was moved before place.
``osascript`` not found
    → ``osascript_missing`` — macOS-only feature; osascript ships with macOS.
Save works but ``photoshop.method: "open"``
    → the still opened as a new document instead of embedding — expected when
      the installed version has no place/duplicate path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from .coerce import env_flag

PHOTOSHOP_BUNDLE_ID = "com.adobe.Photoshop"

MARKER_PLACED = "CLIPSTASH_PLACED"
MARKER_OPENED = "CLIPSTASH_OPENED"
MARKER_NOT_RUNNING = "CLIPSTASH_NOT_RUNNING"
MARKER_NO_DOCUMENT = "CLIPSTASH_NO_DOCUMENT"
MARKER_ERROR = "CLIPSTASH_ERROR:"
MARKER_NO_ARG = "CLIPSTASH_NO_ARG"


def _placement_script(body: str) -> str:
    """Wrap one method's ``tell`` body (newline-terminated lines) in the shared
    argv check, POSIX file, and is-running skeleton."""
    return f"""on run argv
\tif (count of argv) < 1 then
\t\treturn "{MARKER_NO_ARG}"
\tend if
\tset imagePath to item 1 of argv
\tset imageFile to POSIX file imagePath
\tif application id "{PHOTOSHOP_BUNDLE_ID}" is running then
\t\ttell application id "{PHOTOSHOP_BUNDLE_ID}"
{body}\t\tend tell
\telse
\t\treturn "{MARKER_NOT_RUNNING}"
\tend if
end run"""


# Tuple of (name, script_text). Order matters: the first script that compiles
# against the installed Photoshop dictionary and produces a definitive result
# wins; compile failures move on to the next script.
_PLACEMENT_SCRIPTS: tuple[tuple[str, str], ...] = (
    (
        "place",
        _placement_script(f"""\t\t\ttry
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
"""),
    ),
    (
        "duplicate",
        _placement_script(f"""\t\t\ttry
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
"""),
    ),
    (
        "open",
        _placement_script(f"""\t\t\ttry
\t\t\t\topen imageFile showing dialogs never
\t\t\ton error errMsg
\t\t\t\treturn "{MARKER_ERROR} " & errMsg
\t\t\tend try
\t\t\treturn "{MARKER_OPENED}"
"""),
    ),
)


def is_macos() -> bool:
    return sys.platform == "darwin"


def env_photoshop_enabled() -> bool:
    """True when CLIPSTASH_PHOTOSHOP requests auto-place (1/true/yes/on)."""
    return env_flag("CLIPSTASH_PHOTOSHOP")


def _fail(reason: str, error: str, image_path: Path) -> dict[str, Any]:
    """The one failure shape every reason code is returned in."""
    return {"ok": False, "error": error, "reason": reason, "image": str(image_path)}


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
            return _fail("photoshop_not_running", "Photoshop is not running", image_path)
        if MARKER_NO_DOCUMENT in stdout:
            detail = stdout.split(":", 1)[1].strip() if ":" in stdout else ""
            reason = "photoshop_no_document"
            if _looks_like_timeout(detail):
                reason = "photoshop_timeout"
            error = f"Photoshop is running but has no open document{f': {detail}' if detail else ''}"
            return _fail(reason, error, image_path)
        if stdout.startswith(MARKER_ERROR):
            detail = stdout[len(MARKER_ERROR):].strip()
            return _error_result(detail, image_path, script_name)
        # No marker and exit 0: unexpected, treat as failure.
        return _fail(
            "photoshop_unexpected_output",
            f"osascript returned no result marker ({stdout or 'empty output'})",
            image_path,
        )

    # Non-zero exit. Compile failures mean the verb is not in this Photoshop
    # version's dictionary — the caller moves on to the next script.
    if _is_compile_failure(stderr):
        return None
    if _looks_like_not_installed(stderr):
        return _fail(
            "photoshop_not_installed",
            "Adobe Photoshop is not installed (com.adobe.Photoshop not found)",
            image_path,
        )
    return _error_result(stderr or f"osascript exited {completed.returncode}", image_path, script_name)


def _error_result(message: str, image_path: Path, script_name: str) -> dict[str, Any]:
    reason = "photoshop_osascript_failed"
    if _looks_like_automation_denied(message):
        reason = "photoshop_automation_denied"
    elif _looks_like_timeout(message):
        reason = "photoshop_timeout"
    elif "no document" in message.lower():
        reason = "photoshop_no_document"
    return _fail(reason, f"Photoshop {script_name} failed: {message}", image_path)


def place_in_photoshop(image_path: str | Path, *, timeout: float = 120.0) -> dict[str, Any]:
    """Place a PNG into Photoshop's frontmost document (macOS only).

    Returns ``{"ok": True, "placed": True, ...}`` on success or a clear
    ``{"ok": False, "error": ..., "reason": ...}`` dict on failure. Never raises
    for Photoshop-side problems.
    """
    path = Path(image_path).expanduser()
    if not is_macos():
        return _fail("unsupported_platform", "Photoshop place mode is macOS-only", path)
    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        return _fail("missing_image", f"image not found: {path}", path)

    compile_failures: list[str] = []
    for script_name, script in _PLACEMENT_SCRIPTS:
        # The image path travels as an argv item, never interpolated into the script.
        command = ["osascript", "-e", script, str(path)]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return _fail("osascript_missing", "osascript not found (macOS-only feature)", path)
        except subprocess.TimeoutExpired:
            return _fail(
                "photoshop_timeout",
                f"osascript timed out after {timeout:g}s waiting for Photoshop",
                path,
            )

        result = _classify_script_result(script_name, completed, path)
        if result is not None:
            return result
        compile_failures.append(f"{script_name}: {completed.stderr.strip()}")

    # Every script failed to compile against the installed Photoshop dictionary.
    return {
        **_fail(
            "photoshop_unsupported",
            "no Photoshop AppleScript path available for this Photoshop version",
            path,
        ),
        "details": compile_failures,
    }
