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
| 7 | 2026-09-25 | Worker | deepseek-pty --timeout 900 exited 124 mid Slice 5; commits were already clean — raise timeout for large slices or split briefs. | Low | Open |
