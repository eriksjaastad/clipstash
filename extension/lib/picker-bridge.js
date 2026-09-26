// clipstash picker bridge content script (declared in manifest.json for
// http://127.0.0.1:8787/picker/*). The picker page cannot reach
// chrome.storage, so it dispatches a `clipstash:chosen` CustomEvent after a
// frame is chosen. This bridge forwards the chosen packet to the service
// worker, which appends it to the extension clipboard history through the
// same pushHistory path as single captures.
(() => {
  window.addEventListener("clipstash:chosen", (event) => {
    const packet = event && event.detail;
    if (!packet) {
      return;
    }
    try {
      chrome.runtime.sendMessage({ type: "APPEND_HISTORY", packet });
    } catch (_error) {
      // The service worker may be asleep or the extension reloaded; the
      // helper's pending-history queue is the fallback path.
    }
  });
})();
