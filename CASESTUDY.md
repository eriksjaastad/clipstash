# Case study: clipstash for creator banner batches

**clipstash** is a Chrome-first tool that saves a **video still + title + source URL** as one reusable packet. This note is the product story: a real job (promotional banners for a creator across their social accounts), what hurts without a tool, and what the public extract keeps.

Public repo: [eriksjaastad/clipstash](https://github.com/eriksjaastad/clipstash).  
Companion plan: [PLAN.md](./PLAN.md). Known limits and deferred ideas: [ISSUES.md](./ISSUES.md).

![Side-by-side: manual banner workflow vs clipstash](./assets/case-study-savings.svg)

*Savings grow with volume. At five banners the tool helps; at a hundred per category it compounds.*

---

## The job

You are tasked with making a bunch of promotional banners for a creator. You need to go through their videos — often across **YouTube, TikTok, Instagram, and Twitter/X** — pick usable frames, drop them into a Photoshop template (layer masks, layout), export finished banners, and keep a reliable log of what you used and where it came from.

That is not a one-off screenshot. It is a **batch** job. The more you have to make, the more the friction matters. Making five is mildly annoying. Making a hundred per category is where a purpose-built loop pays for itself.

---

## Without a tool (the expensive loop)

For each video you would typically:

1. Open the video page, scrub to a usable moment, take a screenshot.
2. Open image software (Photoshop), place the still, name the layer somehow.
3. Drag it into your template / layer mask, finish the banner, export.
4. Try to remember or re-find the page URL when the client wants a remake.
5. Maintain your own YAML / spreadsheet / notes so the run is auditable.

Repeat across four sites and dozens or hundreds of videos. Context switches, naming drift, and lost provenance are the real cost — not the single click of “screenshot.”

---

## With clipstash (the loop we kept)

1. Go to a video page and trigger capture (burst around the pause).
2. Pick the frame from a short review screen.
3. The helper writes a **packet**: still + `record.yaml` (title, page URL, source URL, site, capture method, …).
4. Optional Photoshop place puts the still into the frontmost document as a layer (finish / export stays yours — we did not build the catalogue finish pipeline into the public tool).
5. As you go, the packet log is the run record; export to CSV (and from there Excel or whatever you need).

Clipboard history in the extension is a convenience. The **packet** is the source of truth.

```text
~/Clipstash/packets/<id>/
  still.png
  record.yaml   # title, URLs, site, capture_method, timestamp, …
```

Site adapters (YouTube, X, Instagram, TikTok, plus a generic fallback) own “what is the title and canonical URL on this page?” Capture code owns “get me a still.” Keeping those separate is what turned a client-specific grabber into something strangers can extend.

---

## Why volume is the point

| Batch size | What you feel |
|------------|----------------|
| ~5 banners | Some time saved; nice to have |
| ~100 / category | Capture, naming, and “click back to remake” stop being the bottleneck |

We built (and used) this class of tool when the assignment was large. The case for clipstash is not “one prettier screenshot.” It is **repeatable provenance at scale**: still, title, URL, and a log you can export — every time.

---

## Architecture that survived the extract

```text
Chrome extension (MV3)
  adapters → capture / burst → clipboard history
        │
        ▼  localhost HTTP
Local helper (clipstashd)
  write packets · burst picker · CSV · optional Photoshop
```

Why a helper? Browser canvas capture is fast when it works, but cross-origin `<video>` (common on YouTube) taints the canvas. Prefer media-native frames (download/decode + ffmpeg) when you can; keep a visible-tab + crop fallback when you cannot. Disk write and a multi-frame picker are cleaner outside the extension process.

v1 capture ladder:

| Path | When |
|------|------|
| In-page canvas | Untainted `<video>` draw |
| Helper ffmpeg burst | Tainted burst: fetch media → ±7 × 0.15s PNGs → picker |
| Visible-tab + crop | Fallback: full-tab shot cropped to the video’s on-screen rect |

Install aims at “boring for strangers”: Homebrew formula and/or a local PyInstaller binary plus LaunchAgent. We are **not** joining the Apple Developer Program or notarizing downloadables; brew/source (and local unsigned builds) are the supported paths.

---

## What we deliberately left behind (and what is next)

| Left in private / deferred | Why |
|----------------------------|-----|
| Style / face scoring gates | Tuned to one corpus; not the packet product |
| Catalogue numbering / finish export directories | Client ops; public v1 stops at packet + optional place |
| Review dashboard / timed links | Client delivery surface |
| Real captures, sheets, worklists | Never publish |
| Windows/Linux helper parity | macOS first |
| Chrome Web Store polish | Sideload docs first |

**On the product backlog (idea pile → cards):**

- Web-safe still / layer names (adapter defaults for the four sites + optional type-in we always slugify).
- Packets organized by **site folder** (e.g. YouTube/…) with stills named from the **video title** (web-safe), so a folder listing is readable and the YAML still holds the page URL for remakes.
- Export polish around that YAML → CSV / spreadsheet story.
- Creator video-grid overlay: **green check** on thumbnails already in the packet log (huge when you are making ~100 per category — we had a version of this in the original private tool).

The extract rule that kept us honest: **rewrite for the packet model; do not scrub-and-push the private tree.** Then grow the public tool toward the batch workflow the case study describes.

---

## How it was built (short)

- Plan and board first (`PLAN.md`, Kanban), then a public empty repo and synthetic demo packets only.
- Slices shipped as small PRs: schema → extension + history → adapters → burst picker → Photoshop → install → polish.
- Coding seat: local DeepSeek as Worker under a stronger orchestrator; no Cursor cloud for implementation. Behaviour docs live next to code with drift-guard tests; markdown keeps the why/how story.

The product lesson: **end a client job by naming the transferable object** (here, the packet), rewrite around it, leave engagement-specific machinery in the archive, and let volume — not novelty — justify the next features.

---

## What “done” looks like for v1

- Packets on disk with stable records and CSV export
- Four site adapters + generic fallback
- Clipboard history in the extension
- Burst + picker, including ffmpeg path for cross-origin video
- Optional macOS Photoshop place with clear automation errors
- Homebrew / single-binary install story
- MIT license, public GitHub, no client data in tree

v1 is the capture + packet core. Matching the full “banner factory” story (site folders, title-slug names, grid checks, richer export) is intentional follow-on work — update the code to match this case study as those cards ship.

---

## If you are extracting your own tool

1. Write the one-line **job** before you copy files (ours: batch creator banners across social video).
2. Name the source-of-truth artifact (ours is the packet folder + record).
3. Draw a hard “never publish” list on day one.
4. Prefer adapters and capture modes you can explain in a table over a monolith grab script.
5. Ship a stranger install path before you polish platform edge cases (TCC, Store). Skip paid Apple notarization unless you need downloaded prebuilts.
6. Optimize for the **hundredth** capture, not the demo of five.

clipstash exists because the client work ended — and because the still + title + URL habit did not.
