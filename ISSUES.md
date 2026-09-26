# clipstash — non-blocking issues log

Running list of problems deferred while shipping. Review with Erik at end of run.

| # | When | Area | Issue | Severity | Status |
|---|------|------|-------|----------|--------|
| 1 | 2026-09-25 | Extension capture | Drawing a cross-origin `<video>` (e.g. googlevideo on YouTube) to canvas may taint the canvas and make `toDataURL()` throw. Fixed with a `chrome.tabs.captureVisibleTab` fallback under the existing `activeTab` permission; remaining limitation: v1 keeps the full tab as the still (no video crop yet), labeled `capture_method: visible_tab` in the record. | Medium | Fixed (crop deferred) |
| 2 | 2026-09-25 | Helper install | Install docs + LaunchAgent template shipped in slice 7 (PR #4: `docs/install.md`, `packaging/macos/`, `scripts/dev_up.sh`). Homebrew formula / single-binary publish still deferred. | Low | Fixed (brew deferred) |
| 3 | 2026-09-25 | Extension adapters | X / Instagram / TikTok adapters shipped in slice 4; all four site adapters plus the generic fallback are live now. | Low | Fixed |
| 4 | 2026-09-25 | Options | Options page documents the helper-side save root but has no UI to change it (helper-side for v1 by design). | Low | Open |
| 5 | 2026-09-25 | Burst picker | Cross-origin `<video>` (e.g. googlevideo on YouTube) taints every burst canvas, so the burst session falls back to a single `visible_tab` frame (`capture_method: burst_visible_tab`). No multi-frame native burst on googlevideo by design. | Medium | Fixed (by design) |
| 6 | 2026-09-25 | Burst picker | A frame chosen in the picker is written to disk by the helper, but the picker page cannot reach `chrome.storage`, so that packet is not appended to the extension clipboard history. | Low | Open |
| 7 | 2026-09-25 | Worker | deepseek-pty exited 124 mid-slice twice: Slice 5 (--timeout 900, commits already clean) and Slice 6 (--timeout 1200, code landed uncommitted and was finished/committed by the executor). Prefer longer timeouts or split briefs for large slices. | Low | Open |
| 8 | 2026-09-25 | Photoshop place | macOS TCC/Automation: the first `osascript` Apple Event to Photoshop requires user consent (System Settings → Privacy & Security → Automation). Denied consent surfaces as error `-1743`; the helper maps it to `photoshop_automation_denied` and keeps running. Non-blocking: document-only (see docs/photoshop.md). | Low | Open |
| 9 | 2026-09-25 | Photoshop place | Photoshop 2025/2026 removed the `place` AppleEvent from the scripting dictionary (verified against the PS 2026 sdef). The helper falls back to a `duplicate`-layers script, then to `open` (new document); if none compile it reports `photoshop_unsupported`. Also, document AppleEvents can be slow/busy on a loaded PS install (observed `AppleEvent timed out` -1712 while testing), which the helper reports as `photoshop_timeout` instead of hanging. Non-blocking; a UXP plugin rewrite is out of scope for this slice. | Low | Open |
