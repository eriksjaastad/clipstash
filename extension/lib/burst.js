// clipstash burst capture content script (injected by the service worker on
// demand, after lib/adapters.js and lib/frame.js).
//
// Steps video.currentTime by ±STEP × N around the current position and
// canvas-captures each frame to a PNG data URL. If the canvas is tainted, it
// returns metadata plus `tainted: true`, `mediaUrl` and `cropRect` so
// background.js can try a helper-native ffmpeg burst before falling back to a
// single chrome.tabs.captureVisibleTab shot (see lib/frame.js).
//
// The final expression is a Promise; chrome.scripting.executeScript waits for
// it and returns the resolved object to background.js.

(async () => {
  const STEP = 0.15;
  const N = 7;

  try {
    const frame = globalThis.ClipStashFrame;
    if (!frame) {
      return { ok: false, error: "no frame helpers available" };
    }
    const prepared = await frame.prepare();
    if (!prepared.ok) {
      return prepared;
    }
    const { video, metadata } = prepared;

    const center = typeof video.currentTime === "number" ? video.currentTime : 0;
    const canSeek = Boolean(video.seekable && video.seekable.length > 0);
    const offsets = [];
    for (let i = -N; i <= N; i += 1) offsets.push(i * STEP);
    const targets = canSeek ? seekTargets(video, center, offsets) : [center];
    const wasPaused = video.paused;

    const frames = [];
    let tainted = false;

    try {
      if (!wasPaused) video.pause();
    } catch (_error) {
      // Some embeds block pause(); keep going with whatever state we have.
    }

    try {
      for (const target of targets) {
        if (canSeek) await seekVideo(video, target);
        try {
          frames.push(frame.captureFrame(video));
        } catch (error) {
          if (frame.isCanvasTaintError(error)) {
            tainted = true;
            break;
          }
          throw error;
        }
      }
    } finally {
      // Best-effort restore of playback position and state, even when a
      // non-taint capture error aborts the loop.
      try {
        if (canSeek) await seekVideo(video, center);
        if (!wasPaused) video.play();
      } catch (_error) {
        // Restore is best-effort only.
      }
    }

    if (tainted) {
      if (frames.length === 0) {
        return {
          ok: false,
          tainted: true,
          ...metadata,
          mediaUrl: video.currentSrc || video.src || "",
          captureMethod: "burst_visible_tab",
          cropRect: frame.videoCropRect(video),
          error: "burst canvas capture failed (tainted)",
        };
      }
      return {
        ok: true,
        ...metadata,
        frames,
        captureMethod: "burst_canvas",
        partial: true,
        note: "some burst frames dropped (tainted)",
      };
    }

    return { ok: true, ...metadata, frames, captureMethod: "burst_canvas" };
  } catch (error) {
    return {
      ok: false,
      error: `burst capture failed: ${error && error.message ? error.message : error}`,
    };
  }
})();

function seekTargets(video, center, offsets) {
  const start = video.seekable.start(0);
  const end = video.seekable.end(0);
  const targets = [];
  const seen = new Set();
  for (const offset of offsets) {
    const target = clamp(center + offset, start, end);
    const key = Number.isFinite(target) ? target.toFixed(4) : String(target);
    if (!seen.has(key)) {
      seen.add(key);
      targets.push(target);
    }
  }
  return targets;
}

function clamp(value, min, max) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min >= max) return value;
  return Math.min(Math.max(value, min), max);
}

function seekVideo(video, time) {
  return new Promise((resolve) => {
    let settled = false;
    let timer = null;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      video.removeEventListener("seeked", finish);
      resolve(true);
    };
    timer = setTimeout(finish, 1000);
    video.addEventListener("seeked", finish);
    try {
      video.currentTime = time;
    } catch (_error) {
      finish();
    }
  });
}
