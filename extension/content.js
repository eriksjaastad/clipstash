// clipstash content script (injected by the service worker on demand).
//
// Finds the page's video via the matching site adapter, extracts packet
// metadata, and captures the current frame to a canvas → PNG data URL.
// If the canvas is tainted by cross-origin media (e.g. googlevideo on
// YouTube), the script returns metadata plus `tainted: true` so background.js
// can fall back to chrome.tabs.captureVisibleTab. That fallback signal also
// carries `cropRect` — the video element's on-screen CSS box ×
// devicePixelRatio — so the helper can crop the full-tab PNG to the video
// rectangle. Crop caveat: letterboxed / object-fit videos may include black
// bars inside the element box; the CSS box is the best practical crop without
// decoding the media.
//
// The final expression is a Promise; chrome.scripting.executeScript waits
// for it and returns the resolved object to background.js.

(async () => {
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

    try {
      return {
        ok: true,
        ...metadata,
        imageDataUrl: captureFrame(video),
        captureMethod: "canvas",
      };
    } catch (error) {
      if (isCanvasTaintError(error)) {
        return {
          ok: false,
          tainted: true,
          ...metadata,
          captureMethod: "visible_tab",
          cropRect: videoCropRect(video),
          error: `canvas capture failed (tainted): ${error && error.message ? error.message : error}`,
        };
      }
      throw error;
    }
  } catch (error) {
    return { ok: false, error: `frame capture failed: ${error && error.message ? error.message : error}` };
  }
})();

async function waitForVideoReady(video, timeoutMs = 4000) {
  const start = Date.now();
  while (video && video.videoWidth === 0 && video.videoHeight === 0) {
    if (Date.now() - start >= timeoutMs) {
      throw new Error("video has no frame dimensions (not playing?)");
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
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

function videoCropRect(video) {
  try {
    const rect = video.getBoundingClientRect();
    const rawDpr = window.devicePixelRatio;
    const dpr = Number.isFinite(rawDpr) && rawDpr > 0 ? rawDpr : 1;
    return {
      x: rect.left,
      y: rect.top,
      width: rect.width,
      height: rect.height,
      dpr,
    };
  } catch (_error) {
    // A missing rect must not break the taint fallback; the helper saves the
    // full tab when cropRect is absent.
    return null;
  }
}
