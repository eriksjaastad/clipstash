// clipstash content script (injected by the service worker on demand, after
// lib/adapters.js and lib/frame.js).
//
// Captures the current frame to a PNG data URL. If the canvas is tainted, it
// returns metadata plus `tainted: true` and `cropRect` so background.js can
// fall back to chrome.tabs.captureVisibleTab (see lib/frame.js).
//
// The final expression is a Promise; chrome.scripting.executeScript waits
// for it and returns the resolved object to background.js.

(async () => {
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

    try {
      return {
        ok: true,
        ...metadata,
        imageDataUrl: frame.captureFrame(video),
        captureMethod: "canvas",
      };
    } catch (error) {
      if (frame.isCanvasTaintError(error)) {
        return {
          ok: false,
          tainted: true,
          ...metadata,
          captureMethod: "visible_tab",
          cropRect: frame.videoCropRect(video),
          error: `canvas capture failed (tainted): ${error && error.message ? error.message : error}`,
        };
      }
      throw error;
    }
  } catch (error) {
    return { ok: false, error: `frame capture failed: ${error && error.message ? error.message : error}` };
  }
})();
