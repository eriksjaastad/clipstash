// clipstash content script (injected by the service worker on demand).
//
// Finds the page's video via the matching site adapter, extracts packet
// metadata, and captures the current frame to a canvas → PNG data URL.
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
    const imageDataUrl = captureFrame(video);

    return {
      ok: true,
      title: info.title || document.title || location.href,
      sourceUrl: info.sourceUrl || location.href,
      pageUrl: location.href,
      timestampSec: typeof info.currentTime === "number" ? info.currentTime : undefined,
      site: adapter.id,
      imageDataUrl,
    };
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
