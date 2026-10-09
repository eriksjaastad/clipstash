# clipstash — product & extract plan

**Card:** #7593  
**Board:** http://localhost:8000/kanban/clipstash  
**Public repo (when Erik says go):** `eriksjaastad/clipstash`  
**Precursor (private, archived):** `fci/fci-kwiky-thumbs`  
**Locked:** 2026-09-25  

> Not a clipboard manager. Name collides with many ClipStash clipboard apps — README must lead with “video still + title + URL packets.”

---

## 1. One-line product

**clipstash** is a Chrome-first tool that captures a still from a page video, saves **image + title + source URL** as one packet, and makes that packet easy to reuse (clipboard history, spreadsheet export, optional Photoshop).

## 2. Core packet (source of truth)

The record.yaml schema and on-disk layout live in the `helper/packets.py` module docstring.

Configurable root via extension options / helper config.

**Exports (derived, never the source of truth):**

| Format | Use |
|--------|-----|
| CSV / TSV | Spreadsheet people |
| XLSX | Optional later |
| Clipboard pack | Title + URL (+ optional markdown) |
| Folder zip | Share a batch |

## 3. Architecture

```
┌─────────────────────────────┐
│  Chrome extension (MV3)     │
│  - site adapters            │
│  - burst / single capture   │
│  - clipboard + history UI   │
│  - options (save root, etc) │
└──────────────┬──────────────┘
               │ localhost HTTP
               ▼
┌─────────────────────────────┐
│  Local helper (clipstashd)  │
│  - write packets to disk    │
│  - serve burst picker UI    │
│  - health: GET /health      │
│  - export CSV on demand     │
└──────────────┬──────────────┘
               │ optional
               ▼
┌─────────────────────────────┐
│  Photoshop mode (macOS)     │
│  - place still into open    │
│    document (Apple Events)  │
└─────────────────────────────┘
```

### Why a local helper

Precursor used `roster_server.py` + Shortcuts/`grab.sh` + ffmpeg for native-resolution bursts and a picker page. That pattern stays: the extension should not be the only place frames live, and disk write + picker are cleaner in a small local process.

**Install bar for strangers (must be boring):**

1. One install path: Homebrew formula *or* single signed macOS binary *or* `curl | sh` installer that drops `clipstashd` + LaunchAgent.
2. Extension popup shows **Helper: running / not running** with a **Start** / **Install** link.
3. Helper binds `127.0.0.1` only; no auth needed beyond localhost (document the risk; optional token later).

**v0 escape hatch:** extension-only single-frame capture (canvas/`captureVisibleTab` or `<video>` draw) writing via the helper if present, or chrome.downloads if helper is down — so the product isn’t dead before helper install is polished.

## 4. Chrome extension (product surface)

### Responsibilities

- Detect active video on supported sites.
- Run site adapter: title, canonical video URL, current time.
- Trigger capture: single frame or burst (±N frames around pause).
- Open picker (helper UI) or accept auto-best later.
- On save: ask helper to write packet; push **title + URL** to system clipboard; append to **in-plugin history** (last N, e.g. 20).
- History panel: re-copy title, URL, or combined text; open packet folder; reveal image.
- Creator grids: on YouTube / TikTok / Instagram, show a **green check** on thumbs already in the packet log (#7691; canonical URL match against `GET /packets`; X out of scope).

### Clipboard history (explicit product requirement)

Most people overwrite the clipboard before pasting. History is first-class:

- Stored in `chrome.storage.local` (and mirrored in helper index optional).
- Each entry: `{ id, title, url, text, imagePath?, createdAt }`.
- One-click **Copy title**, **Copy URL**, **Copy both**.

### Site adapters (v1 built-in)

| Site | Title | Video / URL notes |
|------|-------|-------------------|
| YouTube | `document` / player title | `youtu.be` / `watch?v=` canonical; `<video>` or player API time |
| X / Twitter | post text or “video” fallback | status URL; media video element |
| Instagram | reel/post caption or account+id | permalink; reel `<video>` |
| TikTok | item description / author | video permalink; `<video>` |

Each adapter implements:

```ts
interface SiteAdapter {
  id: string;
  matches(url: string): boolean;
  extract(ctx): Promise<{ title: string; sourceUrl: string; currentTime?: number }>;
  findVideo(ctx): HTMLVideoElement | null;
}
```

Unsupported sites: generic fallback (page title + location.href + first playing `<video>`), labeled “generic” so quality expectations are clear.

## 5. Capture modes

| Mode | Behavior | v1? |
|------|----------|-----|
| **A. In-page frame** | Draw current `<video>` frame to canvas → PNG | Yes (fast path) |
| **B. Burst via helper** | Helper downloads/streams media or receives frame blobs; ffmpeg or decoder burst; picker UI | Yes (quality path; port from precursor grab) |
| **C. Visible-tab shot** | `chrome.tabs.captureVisibleTab` fallback when the canvas is tainted by cross-origin media (full tab for v1, labeled `capture_method: visible_tab`) | Yes (fallback) |

Precursor lesson: browser chrome and play buttons ruin thumbs — prefer media-native frames (B) when possible; A is good enough for Twitter helpers and quick posts.

## 6. Reuse modes

1. **Post pack (X / Threads / etc.)** — clipboard title+URL + image sitting in packets folder (user attaches from disk or “Copy image” where OS allows).
2. **Catalogue / spreadsheet** — `clipstash export csv`.
3. **Photoshop (optional)** — helper places PNG into front document (port of precursor place/grab → PS). Document as macOS-only mode.

## 7. v1 scope cut

### In v1

- Packet schema + on-disk layout  
- Extension + 4 adapters + generic fallback  
- Clipboard + history  
- Helper: health, write packet, burst picker, CSV export  
- README with anti-clipboard-manager positioning  
- Synthetic demo packets (no real client data)  
- MIT or Apache-2.0  

### Explicitly out of v1

- Style gate / YuNet scoring (`source.py` / `style.py`)  
- Catalogue numbering / finish pipeline  
- Review dashboard / timed review links / GCP deploy  
- Kwiky-specific naming (`SFWTHUMBS_KWIKYCONTENT`)  
- Real `thumbs.json`, worklists, performer URLs  
- Windows/Linux helper parity (macOS first; others later)  
- Chrome Web Store listing polish (sideload docs first)  

### Later upgrades (from precursor)

- Source-quality scoring before style  
- Skin-pixel style gate + baseline calibration  
- Numbered finished catalogue  
- Generic review pack  

## 8. Extract map (private → public)

| Precursor | Public clipstash | Action |
|-----------|------------------|--------|
| `extension/*` | `extension/` | Rewrite: drop Kwiky URLs; add adapter interface |
| `grab.py` / `grab.sh` / Shortcuts | `helper/` capture path | Generalize; no Shortcuts required for strangers |
| `roster_server.py` picker | `helper/` picker UI | Keep UX; scrub branding |
| `place.py` (Photoshop) | `helper/photoshop` optional | Feature-flag |
| `catalog.py`, `finish.py`, naming | — | Defer |
| `source.py`, `style.py` | — | Defer as “quality pack” |
| `dashboard.py`, review deploy | — | Defer / never with client names |
| `thumbs.json`, captures, sheets | — | **Never publish** |

**Rule:** rewrite for the packet model; do not scrub-and-push the private tree.

## 9. Repo layout (proposed)

```
clipstash/
  README.md
  PLAN.md                 # this file
  LICENSE
  extension/              # MV3
  helper/                 # clipstashd (Python or Go — decide at implement)
  docs/
    adapters.md
    install.md
  examples/packets/       # synthetic only
  scripts/export_csv.py
```

**Helper language decision (implement time):** Python matches precursor speed-to-port; Go/Rust if we want a single binary for install UX. Prefer **one binary** for stranger install even if core logic starts in Python + PyInstaller, then replace.

## 10. Implementation slices (after “go”)

| Slice | Deliverable |
|-------|-------------|
| **0** | Create public `eriksjaastad/clipstash`, LICENSE, README positioning |
| **1** | Packet schema + helper write/read + CSV export + synthetic example |
| **2** | Extension skeleton + clipboard history + helper health |
| **3** | YouTube adapter + in-page single-frame capture |
| **4** | X, Instagram, TikTok adapters |
| **5** | Burst picker path (helper) |
| **6** | Optional Photoshop mode |
| **7** | Install story (LaunchAgent / brew) + docs |

~500 substantive lines per PR where coherent; bundle related slices when small.

## 11. Coding policy

- Worker: DeepSeek Pro via `~/bin/deepseek-pty` on MacBook — never bare `deepseek -x`, never Cursor cloud for implementation.  
- Local exact-HEAD code review before merge.  
- Board work via `pt` only (no direct DB writes).  

## 12. Success criteria for #7593 (plan phase)

- [x] Framing locked (packet core + adapters + clipboard history + helper)  
- [x] Name: **clipstash**; board: http://localhost:8000/kanban/clipstash  
- [x] This `PLAN.md` covers schema, architecture, adapters, v1 cut, extract map  
- [ ] Erik accepts plan (or requests edits)  
- [ ] **Stop** — no public repo until Erik says go  

## 13. Open decisions (Erik)

1. Helper language / single-binary preference for v1 install?  
2. Default save root (`~/Clipstash` vs project-relative)?  
3. License MIT vs Apache-2.0?  
4. Create GitHub repo now or after Slice 1 locally?
