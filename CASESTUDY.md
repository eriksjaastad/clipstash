# Case study: from an ended client job to clipstash

**clipstash** is a Chrome-first tool that saves a **video still + title + source URL** as one reusable packet. This note is how that product came out of a finished private workflow instead of a greenfield idea.

Public repo: [eriksjaastad/clipstash](https://github.com/eriksjaastad/clipstash).  
Companion plan: [PLAN.md](./PLAN.md). Known limits: [ISSUES.md](./ISSUES.md).

---

## The job that ended

A private macOS pipeline existed to pull stills from page video for a client thumbnail workflow: Chrome + a local helper, burst frames around a pause, a picker UI, optional Photoshop place, plus a lot of catalogue and review machinery that only made sense for that engagement.

When the engagement ended, the useful *shape* remained:

- people still want a clean frame from a playing video
- they still want the **title and URL** that go with that frame
- they still overwrite the clipboard before they paste

What should not leave the private tree: client URLs, performer catalogues, review dashboards, scoring tuned to that corpus, or any real capture data.

**clipstash** is the rewrite of the reusable core. It is not a scrub-and-push of the old repo.

---

## Product framing (the hard part)

Early name collision: many other projects called “ClipStash” are clipboard-history apps. This one is not. The README leads with **video still + title + URL packets** on purpose.

The unit of value is the **packet**:

```text
~/Clipstash/packets/<id>/
  still.png
  record.yaml   # title, URLs, site, capture_method, timestamp, …
```

Clipboard history, CSV export, and Photoshop place are *derived* from that packet. The packet is the source of truth.

Site adapters (YouTube, X, Instagram, TikTok, plus a generic fallback) own “what is the title and canonical URL on this page?” Capture code owns “get me a still.” Keeping those separate is what made a client-specific grabber into something strangers can extend.

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

Why a helper at all? Browser canvas capture is fast when it works, but cross-origin `<video>` (common on YouTube) taints the canvas. The private tool already knew the answer: **prefer media-native frames** (download or decode, then ffmpeg) and keep a visible-tab fallback when you cannot. Disk write and a multi-frame picker are also cleaner outside the extension process.

v1 capture ladder:

| Path | When |
|------|------|
| In-page canvas | Untainted `<video>` draw |
| Helper ffmpeg burst | Tainted burst: fetch media (yt-dlp for watch URLs when available) → ±7 × 0.15s PNGs → picker |
| Visible-tab + crop | Fallback: full-tab shot cropped to the video’s on-screen rect |

Install aims at “boring for strangers”: Homebrew formula and/or a PyInstaller single binary plus LaunchAgent. Signing and notarization matter only if you publish a **downloaded** prebuilt binary; brew/source builds avoid that Gatekeeper path for now.

---

## What we deliberately left behind

| Left in private / deferred | Why |
|----------------------------|-----|
| Style / face scoring gates | Tuned to one corpus; not the packet product |
| Catalogue numbering / finish pipeline | Client ops, not stranger tooling |
| Review dashboard / timed links | Client delivery surface |
| Real captures, sheets, worklists | Never publish |
| Windows/Linux helper parity | macOS first |
| Chrome Web Store polish | Sideload docs first |

The extract rule that kept us honest: **rewrite for the packet model; do not scrub-and-push the private tree.**

---

## How it was built (short)

- Plan and board first (`PLAN.md`, Kanban), then a public empty repo and synthetic demo packets only.
- Slices shipped as small PRs: schema → extension + history → adapters → burst picker → Photoshop → install → polish (options UI, docs-in-code, crop, brew/binary, ffmpeg burst).
- Coding seat: local DeepSeek as Worker under a stronger orchestrator; no Cursor cloud for implementation. Behaviour docs live next to code with drift-guard tests; markdown keeps the why/how story.

That process detail matters less than the product lesson: **end a client job by naming the transferable object** (here, the packet), rewrite around it, and leave the engagement-specific machinery in the archive.

---

## What “done” looks like for v1

- Packets on disk with stable records and CSV export
- Four site adapters + generic fallback
- Clipboard history in the extension
- Burst + picker, including ffmpeg path for cross-origin video
- Optional macOS Photoshop place with clear automation errors
- Homebrew / single-binary install story
- MIT license, public GitHub, no client data in tree

Optional later: notarized GitHub Release binaries, quality-scoring as a separate pack, other OSes, Store listing.

---

## If you are extracting your own tool

1. Write the one-line product before you copy files.
2. Name the source-of-truth artifact (ours is the packet folder + record).
3. Draw a hard “never publish” list on day one.
4. Prefer adapters and capture modes you can explain in a table over a monolith grab script.
5. Ship a stranger install path before you polish platform edge cases (TCC, notarization, Store).

clipstash exists because the client work ended — and because the still + title + URL habit did not.
