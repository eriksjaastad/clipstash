# clipstash — non-blocking issues log

Running list of problems deferred while shipping. Review with Erik at end of run.

| # | When | Area | Issue | Severity | Status |
|---|------|------|-------|----------|--------|
| 1 | 2026-09-25 | Extension capture | Drawing a cross-origin `<video>` (e.g. googlevideo on YouTube) to canvas may taint the canvas and make `toDataURL()` throw. Content script reports the error; a `captureVisibleTab` / helper-side decode fallback is deferred. | Medium | Open |
| 2 | 2026-09-25 | Helper install | Homebrew / LaunchAgent / single-binary install story is slice 7; currently `python3 -m helper` only. | Low | Open |
| 3 | 2026-09-25 | Extension adapters | X / Instagram / TikTok adapters are slice 4; only YouTube + generic fallback ship now. | Low | Open |
| 4 | 2026-09-25 | Options | Options page documents the helper-side save root but has no UI to change it (helper-side for v1 by design). | Low | Open |
