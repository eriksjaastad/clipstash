# Photoshop place mode (macOS-only, optional)

clipstash can optionally hand a saved still to Photoshop after a packet is
written. This is a macOS-only convenience feature: the helper runs a small
AppleScript through `osascript` that either places the PNG into Photoshop's
frontmost open document or opens it as a new document. Nothing in the core
capture/save flow requires Photoshop.

## Enable

Any one of:

- **Extension popup** — tick **“Also place in Photoshop”** (default off). The
  checkbox is remembered in `chrome.storage.local` and applies to both
  **Save packet** and **Burst & pick** (for burst, the flag is stored on the
  helper-side burst session, so the picker page applies it when you click a
  frame).
- **Per request** — send `"photoshop": true` in the `POST /packets` or
  `POST /bursts` JSON body.
- **Helper config** — set `CLIPSTASH_PHOTOSHOP=1` before launching, or run
  `clipstashd serve --photoshop`, to place every saved packet automatically.

## Re-place a saved packet manually

```bash
curl -X POST http://127.0.0.1:8787/packets/<packet-id>/place-photoshop
```

Success:

```json
{
  "ok": true,
  "packet_id": "01J…",
  "photoshop": { "ok": true, "placed": true, "image": "/…/still.png", "method": "duplicate" }
}
```

Failure (clear JSON error, helper keeps running):

```json
{
  "ok": false,
  "packet_id": "01J…",
  "error": "Photoshop is not running",
  "photoshop": { "ok": false, "error": "Photoshop is not running", "reason": "photoshop_not_running" }
}
```

When placement is requested during `POST /packets` or the burst picker choose,
the packet save always succeeds even if Photoshop is missing or rejects the
request; the placement outcome is returned in an extra `photoshop` field.

## How it works

The helper tries three AppleScripts in order and stops at the first one that
the installed Photoshop version understands:

1. `place` — legacy Photoshop (CS/CC through ~2021) places the PNG file
   directly into the frontmost document.
2. `duplicate` — modern Photoshop: opens the PNG as a temporary document,
   duplicates its art layers into the frontmost document, then closes the
   temporary document. This preserves the “drop into open PS doc” behaviour.
3. `open` — last resort: opens the PNG as a new document.

The image path is passed as a script argument (`on run argv`), never
interpolated into the script text, so paths with spaces or quotes are safe.
`place_in_photoshop()` in `helper/photoshop.py` returns structured JSON-shaped
dicts for every outcome; it never raises for Photoshop-side failures.

## macOS permissions (TCC / Automation)

macOS blocks Apple Events between apps until the user consents. The first time
the helper tells Photoshop what to do, macOS shows:

> “*Terminal*” (or whatever launched `clipstashd`) wants to control
> “Adobe Photoshop …”. [Don't Allow] [OK]

Click **OK**, otherwise `osascript` fails with an “not authorized” / error
`-1743` message and the helper reports `reason: photoshop_automation_denied`.

Where to check or reset the permission later:

- **System Settings → Privacy & Security → Automation** — find your terminal
  app (or launchd/`clipstashd` host) and make sure **Adobe Photoshop** is
  checked.
- If you granted the wrong app, delete its entry and trigger a place again to
  re-prompt.
- When the helper runs as a LaunchAgent, grant Automation to the program named
  in the plist (`clipstashd`), or start `clipstashd` from Terminal once to
  approve Terminal.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `photoshop_not_running` | Photoshop is not running | Launch Photoshop and open a document, then retry |
| `photoshop_no_document` | Photoshop is running but has no open document | Open/create a document, then retry |
| `photoshop_automation_denied` / `-1743` | macOS Automation permission missing | See permissions above |
| `photoshop_timeout` / `AppleEvent timed out` | Photoshop is busy (modal dialog, hung plug-in) or its scripting interface is slow | Dismiss dialogs/restart Photoshop, retry |
| `photoshop_not_installed` / `-1728` | No app with bundle id `com.adobe.Photoshop` | Install Photoshop; the feature is otherwise optional |
| `photoshop_unsupported` | None of the AppleScripts compiled against this Photoshop version | Non-blocking; see `ISSUES.md` |
| save works but `photoshop.method: "open"` | Photoshop version has no `place`/duplicate path | The still opened as a new document instead of embedding — expected on some versions |

## Known version quirks

- Photoshop 2025/2026 removed the `place` AppleEvent from the scripting
  dictionary; the `duplicate`-layers script is used instead, and the final
  fallback is `open` (new document).
- Photoshop's document AppleEvents can be slow on busy installs; the helper
  waits up to 120 s per script and reports a timeout instead of hanging.
- Not supported on Windows (out of scope); a UXP plugin would be the
  cross-platform successor.
