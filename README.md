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
4. Open a YouTube video (or a video post on X/Twitter, Instagram, or TikTok),
   click the clipstash toolbar icon, and press **Save packet**. The still +
   record are written by the helper, and the title + URL are copied to the
   clipboard and added to the history list.

The popup shows whether the helper is running; start `clipstashd` first.

### Site adapters

| Adapter id | Sites | Notes |
|------------|-------|-------|
| `youtube` | youtube.com, youtu.be | canonical `watch?v=` URL, player title + time |
| `x` | x.com, twitter.com | canonical status URL, tweet text title |
| `instagram` | instagram.com | canonical reel/post permalink, og:title |
| `tiktok` | tiktok.com | canonical `@user/video/id` permalink, og:title |
| `generic` | any page | page title + URL + first `<video>` (fallback) |

### Capture fallback

The fast path draws the current `<video>` frame to a canvas and records
`capture_method: canvas`. Cross-origin media (e.g. googlevideo on YouTube) can
taint the canvas and make `toDataURL()` throw; when that happens the extension
falls back to `chrome.tabs.captureVisibleTab` and records
`capture_method: visible_tab`. v1 keeps the full tab as the still (no cropping
yet) — see [ISSUES.md](./ISSUES.md) #1.

Permissions stay minimal: `activeTab` is already declared and grants
`captureVisibleTab` when the user invokes the extension from the toolbar, so the
fallback needs no extra `tabs` or host permission.

### Burst & pick

Instead of a single Save, **Burst & pick** steps the video by small deltas
(±0.15s × 7, centered on the current time), canvas-captures each frame, and
opens a local picker page where you click the best frame:

1. Open a video page and click the clipstash toolbar icon.
2. Press **Burst & pick**.
3. The helper stores the burst session in a temp dir and opens
   `http://127.0.0.1:8787/picker/<id>` in a new tab.
4. Click a frame; the helper writes a normal packet (image + title + URL) and
   deletes the session.

The chosen packet records `capture_method: burst_canvas`. When the canvas is
tainted by cross-origin media (e.g. googlevideo on YouTube), the burst session
falls back to a single visible-tab shot labeled `capture_method:
burst_visible_tab` — see [ISSUES.md](./ISSUES.md) #5. Burst sessions expire
after 45 minutes if nothing is chosen.

## Tests

```bash
python3 -m pytest                 # helper: packet schema, storage, HTTP API
node scripts/smoke_extension.mjs  # extension JS: adapters + taint fallback (no browser needed)
```

## License

MIT
