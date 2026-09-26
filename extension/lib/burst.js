// clipstash burst capture content script (injected by the service worker on demand).
//
// Steps video.currentTime by ±STEP × N around the current position and
// canvas-captures each frame to a PNG data URL. If the canvas is tainted by
// cross-origin media (e.g. googlevideo on YouTube), the script returns the
// metadata plus `tainted: true` so background.js can fall back to a single
// chrome.tabs.captureVisibleTab shot for the burst session.
//
// The final expression is a Promise; chrome.scripting.executeScript waits for
// it and returns the resolved object to background.js.

(async () => {
  const STEP = 0.15;
  const N = 7;

  try {
    const adapters = globalThis.ClipStashAdapters;
    const adapter = adapters ? adapters.adapt(location.href) : null;
    if (!adapter) {
      return { ok: false, error: "no site adapter available" };
    }

    const video = adapter.findVideo(document);
    if (!video) {
      return { ok: false, error: "no <video> element found on this page" };
    }

    await waitForVideoReady(video);

    const info = await adapter.extract({ document, location, video });
    const metadata = {
      title: info.title || document.title || location.href,
      sourceUrl: info.sourceUrl || location.href,
      pageUrl: location.href,
      timestampSec: typeof info.currentTime === "number" ? info.currentTime : undefined,
      site: adapter.id,
    };

    const center = typeof video.currentTime === "number" ? video.currentTime : 0;
    const canSeek = Boolean(video.seekable && video.seekable.length > 0);
    const offsets = [];
    for (let i = -N; i <= N; i += 1) offsets.push(i * STEP);
    const targets = canSeek ? offsets.map((offset) => center + offset) : [center];
    const wasPaused = video.paused;

    const frames = [];
    let tainted = false;

    try {
      if (!wasPaused) video.pause();
    } catch (_error) {
      // Some embeds block pause(); keep going with whatever state we have.
    }

    for (const target of targets) {
      if (canSeek) await seekVideo(video, target);
      try {
        frames.push(captureFrame(video));
      } catch (error) {
        if (isCanvasTaintError(error)) {
          tainted = true;
          break;
        }
        throw error;
      }
    }

    // Best-effort restore of playback position and state.
    try {
      if (canSeek) await seekVideo(video, center);
      if (!wasPaused) video.play();
    } catch (_error) {
      // Restore is best-effort only.
    }

    if (tainted) {
      if (frames.length === 0) {
        return {
          ok: false,
          tainted: true,
          ...metadata,
          captureMethod: "burst_visible_tab",
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

function waitForVideoReady(video, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      if (video && video.videoWidth > 0 && video.videoHeight > 0) {
        resolve();
        return;
      }
      if (Date.now() - start >= timeoutMs) {
        reject(new Error("video has no frame dimensions (not playing?)"));
        return;
      }
      setTimeout(check, 100);
    };
    check();
  });
}

function captureFrame(video) {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/png");
}

function isCanvasTaintError(error) {
  return Boolean(
    error &&
      (error.name === "SecurityError" || /taint/i.test(String(error.message || error)))
  );
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
