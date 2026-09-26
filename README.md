# clipstash

**Video still + title + URL packets** — not a clipboard manager.

Chrome-first tool that captures a still from a page video (YouTube, X/Twitter, Instagram, TikTok), saves **image + title + source URL** together, keeps a short clipboard history in the extension, and optionally hands the still to Photoshop.

> Many other projects are also named “ClipStash” and are clipboard-history apps. This repo is different: it stashes **media packets** for posting and catalogues.

## Status

Early public extract. See [PLAN.md](./PLAN.md).

## Board

http://localhost:8000/kanban/clipstash (private Erik board)

## Layout

```
helper/              Python helper (clipstashd): packet schema + local HTTP API
extension/           Chrome MV3 extension: adapters, capture, clipboard history
examples/packets/    Synthetic demo packet (no real client data)
tests/               pytest for the helper
```

## Run the helper

Requires Python 3.10+ and PyYAML.

```bash
pip install -r requirements.txt
python3 -m helper                # serves on http://127.0.0.1:8787
```

Or install the package and use the `clipstashd` entry point:

```bash
pip install -e .
clipstashd
```

Packets are written to `~/Clipstash/packets/<id>/` by default. Override with
`CLIPSTASH_ROOT=/path/to/packets` or `clipstashd --root /path/to/packets`.

Quick check:

```bash
curl http://127.0.0.1:8787/health
# {"ok": true, "version": "0.1.0"}
```

CSV export:

```bash
curl http://127.0.0.1:8787/export.csv
# or: python3 -m helper export --root examples/packets
```

## Load the extension (unpacked, MV3)

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top right).
3. Click **Load unpacked** and select this repo's `extension/` directory.
4. Open a YouTube video, click the clipstash toolbar icon, and press
   **Save packet**. The still + record are written by the helper, and the
   title + URL are copied to the clipboard and added to the history list.

The popup shows whether the helper is running; start `clipstashd` first.

## Tests

```bash
python3 -m pytest
```

## License

MIT
