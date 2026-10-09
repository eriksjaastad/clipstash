// clipstash frame helpers shared by content.js and lib/burst.js.
//
// Injected by background.js after lib/adapters.js and before the capture
// script; exposed as `globalThis.ClipStashFrame`. prepare() finds the page's
// video via the matching site adapter, waits for frame dimensions, and builds
// the packet metadata. captureFrame() draws the current frame to a canvas →
// PNG data URL.
//
// If the canvas is tainted by cross-origin media (e.g. googlevideo on
// YouTube), the capture scripts return metadata plus `tainted: true` and
// `cropRect` — the video element's on-screen CSS box × devicePixelRatio — so
// the helper can crop the full-tab PNG to the video rectangle. Crop caveat:
// letterboxed / object-fit videos may include black bars inside the element
// box; the CSS box is the best practical crop without decoding the media.

globalThis.ClipStashFrame = (() => {
  // Resolves { ok: true, video, metadata }, or { ok: false, error } when there
  // is no adapter or no video. Readiness and extraction errors reject so each
  // capture script can add its own error prefix.
  async function prepare() {
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
    return { ok: true, video, metadata };
  }

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

  return { prepare, captureFrame, isCanvasTaintError, videoCropRect };
})();
