# clipstash

**Video still + title + URL packets** — not a clipboard manager.

Chrome-first tool that captures a still from a page video (YouTube, X/Twitter, Instagram, TikTok), saves **image + title + source URL** together, keeps a short clipboard history in the extension, and optionally hands the still to Photoshop.

> Many other projects are also named "ClipStash" and are clipboard-history apps. This repo is different: it stashes **media packets** for posting and catalogues.

## Status

Early public extract. See [PLAN.md](./PLAN.md); known gaps in [ISSUES.md](./ISSUES.md).

## Board

http://localhost:8000/kanban/clipstash (private Erik board)

## Layout

```
helper/              Python helper (clipstashd): packet schema + local HTTP API
extension/           Chrome MV3 extension: adapters, capture, clipboard history
examples/packets/    Synthetic demo packet (no real client data)
tests/               pytest for the helper
```

## Run

```bash
./scripts/dev_up.sh     # one-command dev env: .venv + deps + serve in foreground
# or, manually:
python3 -m helper       # serves on http://127.0.0.1:8787
```

Full stranger setup (venv, `pip install -e .` / `uv sync`, `clipstashd`, troubleshooting) is in `clipstashd --help`; the `scripts/dev_up.sh` header has the one-shot clone/setup pointers. For a login LaunchAgent, follow the comments in `packaging/macos/com.clipstash.helper.plist`.

## Load the extension (unpacked, MV3)

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked** and select this repo's `extension/` directory.
4. Open a video page, click the toolbar icon, and press **Save packet**.

## Features at a glance

- **Site adapters** — per-site metadata + canonical URLs (YouTube, X, Instagram, TikTok, generic fallback). Code: `extension/lib/adapters.js`.
- **Capture fallback** — canvas capture falls back to `chrome.tabs.captureVisibleTab` when cross-origin media taints the canvas (no cropping yet, see ISSUES.md #1). Code: `extension/content.js`, `extension/background.js`.
- **Burst & pick** — step the video ±7 × 0.15s, capture up to 15 frames, pick one in the helper-hosted picker (taint falls back to a visible-tab shot, see ISSUES.md #5). Code: `extension/lib/burst.js`, `helper/bursts.py`.
- **Photoshop place mode (macOS, optional)** — hand saved stills to Photoshop via the popup checkbox, `"photoshop": true`, or `CLIPSTASH_PHOTOSHOP=1` / `serve --photoshop`. Full setup, permissions and troubleshooting: `helper/photoshop.py` docstring.

## Save root

Packets default to `~/Clipstash/packets`. Change the root from the extension **Options** page (writes `~/Clipstash/config.json`) or with `--root` / `CLIPSTASH_ROOT`; the resolution order lives in `helper/config.py` and `clipstashd --help`.

## Documentation

What a piece of code **does** is documented next to that code and tested against it; why/how we work stays in markdown (PLAN.md, ISSUES.md). `clipstashd --help` is generated from `helper/cli.py`'s module docstring, `helper/photoshop.py` and `helper/config.py` carry their behaviour in their module docstrings, and drift-guard tests (`tests/test_cli_docs.py`, `tests/test_photoshop_docs.py`, `tests/test_config_docs.py`) fail when docs and code disagree. This README is pointers, not a second `--help`.

## Tests

```bash
.venv/bin/pytest -q                 # helper: packet schema, storage, HTTP API
node scripts/smoke_extension.mjs    # extension JS: adapters + taint fallback (no browser)
```

## License

MIT
