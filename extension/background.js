// clipstash MV3 service worker.
//
// Message hub for the popup/picker and owner of the clipboard history in
// chrome.storage.local. Handles HEALTH, SAVE_PACKET (single frame capture),
// BURST_PICK (burst capture + helper picker), APPEND_HISTORY and GET_HISTORY.
// Talks to the local helper at http://127.0.0.1:8787. Canvas-taint falls back
// to captureVisibleTab; the content scripts report a `cropRect` (video element
// CSS box × devicePixelRatio) with the taint signal, which we forward as
// `crop_rect` so the helper can crop the full-tab PNG to the video rectangle.
// For tainted *bursts* we first try the helper's native ffmpeg burst from the
// video's media URL (`burst_ffmpeg`); when the media URL is missing or the
// helper path fails, we fall back to a single cropped visible-tab shot
// (`burst_visible_tab`). Burst-chosen history entries are drained from the
// helper's pending queue so picker tabs don't need direct storage access.

const HELPER_BASE = "http://127.0.0.1:8787";
const HISTORY_KEY = "clipstashHistory";
const HISTORY_MAX = 20;
const PENDING_POLL_INTERVAL_MS = 1000;
const PENDING_POLL_TIMEOUT_MS = 3 * 60 * 1000;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message)
    .then((response) => sendResponse(response))
    .catch((error) => sendResponse({ ok: false, error: String(error) }));
  return true; // async sendResponse
});

async function handleMessage(message) {
  switch (message && message.type) {
    case "HEALTH":
      return checkHealth();
    case "SAVE_PACKET":
      return captureAndSave(Boolean(message.placePhotoshop));
    case "BURST_PICK":
      return captureBurstAndOpenPicker(Boolean(message.placePhotoshop));
    case "APPEND_HISTORY":
      return appendHistory(message.packet);
    case "GET_HISTORY":
      await drainPendingHistory();
      return { ok: true, history: await getHistory() };
    default:
      return { ok: false, error: `unknown message type: ${message && message.type}` };
  }
}

async function checkHealth() {
  try {
    const response = await fetch(`${HELPER_BASE}/health`);
    const payload = await response.json();
    return {
      ok: true,
      helper: payload,
      status: response.status,
    };
  } catch (error) {
    return { ok: false, helper: null, error: String(error) };
  }
}

async function captureAndSave(placePhotoshop) {
  const tab = await getActiveTab();
  if (!tab || !/^https?:/.test(tab.url || "")) {
    return { ok: false, error: "active tab is not a http(s) page" };
  }

  let injection;
  try {
    injection = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["lib/adapters.js", "content.js"],
    });
  } catch (error) {
    return { ok: false, error: `injection failed: ${error}` };
  }

  const captured = injection && injection[0] && injection[0].result;
  if (!captured) {
    return { ok: false, error: "frame capture failed" };
  }

  // Canvas draw of a cross-origin <video> can taint the canvas and make
  // toDataURL() throw. The content script reports `tainted: true` with the
  // metadata it did manage to extract, so we can fall back to a visible-tab
  // screenshot (no new permission needed: activeTab, already declared, grants
  // captureVisibleTab when the user invokes the extension). Its `cropRect`
  // (video CSS box × devicePixelRatio) is forwarded as `crop_rect` so the
  // helper crops the full-tab PNG to the video rectangle before saving.
  if (captured.tainted) {
    return captureVisibleTabAndSave(tab, captured, placePhotoshop);
  }

  if (!captured.ok) {
    return { ok: false, error: captured.error || "frame capture failed" };
  }

  const imageBase64 = stripDataUrlPrefix(captured.imageDataUrl || "");
  const payload = {
    title: captured.title,
    source_url: captured.sourceUrl,
    page_url: captured.pageUrl || captured.sourceUrl,
    site: captured.site || "generic",
    timestamp_sec: captured.timestampSec,
    capture_method: captured.captureMethod || "canvas",
    image_base64: imageBase64,
    photoshop: placePhotoshop,
  };
  return savePacket(payload);
}

async function captureVisibleTabAndSave(tab, meta, placePhotoshop) {
  let imageDataUrl;
  try {
    imageDataUrl = await captureVisibleTabPng(tab.windowId);
  } catch (error) {
    return { ok: false, error: `visible-tab fallback failed: ${error}` };
  }

  const payload = {
    title: meta.title,
    source_url: meta.sourceUrl,
    page_url: meta.pageUrl || meta.sourceUrl,
    site: meta.site || "generic",
    timestamp_sec: meta.timestampSec,
    capture_method: "visible_tab",
    image_base64: stripDataUrlPrefix(imageDataUrl || ""),
    photoshop: placePhotoshop,
  };
  if (meta.cropRect) {
    payload.crop_rect = meta.cropRect;
  }
  return savePacket(payload);
}

async function captureBurstAndOpenPicker(placePhotoshop) {
  const tab = await getActiveTab();
  if (!tab || !/^https?:/.test(tab.url || "")) {
    return { ok: false, error: "active tab is not a http(s) page" };
  }

  let injection;
  try {
    injection = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["lib/adapters.js", "lib/burst.js"],
    });
  } catch (error) {
    return { ok: false, error: `burst injection failed: ${error}` };
  }

  const captured = injection && injection[0] && injection[0].result;
  if (!captured) {
    return { ok: false, error: "burst capture failed" };
  }

  let frames = [];
  let metadata = {};
  let captureMethod = "burst_canvas";

  if (captured.tainted && !captured.ok) {
    // Canvas taint (e.g. googlevideo on YouTube). First try the helper's
    // native ffmpeg burst from the video's media URL (`burst_ffmpeg`); when
    // that is unavailable or fails, fall back to a single visible-tab shot,
    // labeled burst_visible_tab. The content script's `cropRect` is
    // forwarded as `crop_rect` so the helper crops that single frame to the
    // video rectangle.
    metadata = {
      title: captured.title,
      sourceUrl: captured.sourceUrl,
      pageUrl: captured.pageUrl,
      site: captured.site,
      timestampSec: captured.timestampSec,
    };
    const native = await tryNativeBurst(captured, placePhotoshop);
    if (native) {
      return {
        ok: true,
        session_id: native.session_id,
        picker_url: native.picker_url,
        capture_method: native.capture_method || "burst_ffmpeg",
      };
    }
    captureMethod = "burst_visible_tab";
    try {
      const imageDataUrl = await captureVisibleTabPng(tab.windowId);
      frames = [imageDataUrl];
    } catch (error) {
      return { ok: false, error: `visible-tab burst fallback failed: ${error}` };
    }
  } else if (captured.ok) {
    captureMethod = captured.captureMethod || "burst_canvas";
    metadata = {
      title: captured.title,
      sourceUrl: captured.sourceUrl,
      pageUrl: captured.pageUrl || captured.sourceUrl,
      site: captured.site || "generic",
      timestampSec: captured.timestampSec,
    };
    frames = captured.frames || [];
  } else {
    return { ok: false, error: captured.error || "burst capture failed" };
  }

  if (!frames.length) {
    return { ok: false, error: "burst produced no frames" };
  }

  const payload = {
    title: metadata.title,
    source_url: metadata.sourceUrl,
    page_url: metadata.pageUrl,
    site: metadata.site,
    timestamp_sec: metadata.timestampSec,
    capture_method: captureMethod,
    frames,
    photoshop: placePhotoshop,
  };
  if (captured.cropRect) {
    payload.crop_rect = captured.cropRect;
  }

  let response;
  try {
    response = await fetch(`${HELPER_BASE}/bursts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    return { ok: false, error: `helper unreachable: ${error}` };
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.ok) {
    return { ok: false, error: body.error || `helper returned ${response.status}` };
  }

  try {
    await createTab(body.picker_url);
  } catch (error) {
    return { ok: false, error: `opening picker tab failed: ${error}` };
  }
  startPendingHistoryPolling();
  return { ok: true, session_id: body.session_id, picker_url: body.picker_url };
}

// Try the helper's native ffmpeg burst (`POST /bursts/ffmpeg`) for a tainted
// canvas when the content script reported a media URL. Returns the picker
// result on success, or null when the path is unavailable so the caller can
// fall back to the single visible-tab shot. Never throws.
async function tryNativeBurst(captured, placePhotoshop) {
  const mediaUrl = String(captured.mediaUrl || "").trim();
  if (!/^https?:\/\//i.test(mediaUrl)) {
    return null;
  }
  let response;
  try {
    response = await fetch(`${HELPER_BASE}/bursts/ffmpeg`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        media_url: mediaUrl,
        timestamp_sec: captured.timestampSec,
        title: captured.title,
        source_url: captured.sourceUrl,
        page_url: captured.pageUrl,
        site: captured.site,
        photoshop: placePhotoshop,
      }),
    });
  } catch (_error) {
    return null;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.ok) {
    console.warn(
      "clipstash: native ffmpeg burst unavailable, falling back to visible_tab:",
      body.error || `helper returned ${response.status}`
    );
    return null;
  }
  try {
    await createTab(body.picker_url);
  } catch (_error) {
    return null;
  }
  startPendingHistoryPolling();
  return {
    ok: true,
    session_id: body.session_id,
    picker_url: body.picker_url,
    capture_method: body.capture_method || "burst_ffmpeg",
  };
}

function createTab(url) {
  return new Promise((resolve, reject) => {
    chrome.tabs.create({ url }, (tab) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
      } else {
        resolve(tab);
      }
    });
  });
}

// Callback-style wrapper: works on every Chrome MV3 build regardless of
// whether captureVisibleTab's promise form is available.
function captureVisibleTabPng(windowId) {
  return new Promise((resolve, reject) => {
    chrome.tabs.captureVisibleTab(windowId, { format: "png" }, (dataUrl) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
      } else {
        resolve(dataUrl);
      }
    });
  });
}

function stripDataUrlPrefix(value) {
  return (value || "").includes(",") ? value.split(",", 2)[1] : value;
}

async function savePacket(payload) {
  let response;
  try {
    response = await fetch(`${HELPER_BASE}/packets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    return { ok: false, error: `helper unreachable: ${error}` };
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.ok) {
    return { ok: false, error: body.error || `helper returned ${response.status}` };
  }

  const record = body.packet;
  await pushHistory(historyEntryFromPacket(record));
  return { ok: true, packet: record, photoshop: body.photoshop || null };
}

function historyEntryFromPacket(record) {
  const clipboard = record.clipboard || {};
  return {
    id: record.id,
    title: record.title,
    url: record.source_url,
    text: clipboard.text || `${record.title}\n${record.source_url}`,
    createdAt: record.created_at,
  };
}

async function appendHistory(packet) {
  if (!packet || !packet.id || !packet.title || !packet.source_url) {
    return { ok: false, error: "APPEND_HISTORY requires a packet with id/title/source_url" };
  }
  await pushHistory(historyEntryFromPacket(packet));
  return { ok: true };
}

// Fallback path: the helper enqueues burst-chosen history entries while the
// picker tab is open, and we drain them here. This covers the window between
// a chosen frame and the content-script bridge (or a service-worker restart).
async function drainPendingHistory() {
  let response;
  try {
    response = await fetch(`${HELPER_BASE}/history/pending`);
  } catch (error) {
    return { ok: false, error: `helper unreachable: ${error}` };
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.ok) {
    return { ok: false, error: body.error || `helper returned ${response.status}` };
  }
  const entries = Array.isArray(body.entries) ? body.entries : [];
  const appended = [];
  for (const entry of entries) {
    const normalized = normalizeHistoryEntry(entry);
    if (normalized) {
      await pushHistory(normalized);
      appended.push(normalized.id);
    }
  }
  return { ok: true, appended };
}

function normalizeHistoryEntry(entry) {
  if (!entry || !entry.id || !entry.title || !entry.url) {
    return null;
  }
  return {
    id: String(entry.id),
    title: String(entry.title),
    url: String(entry.url),
    text: String(entry.text || `${entry.title}\n${entry.url}`),
    createdAt: String(entry.createdAt || ""),
  };
}

let pendingPollTimer = null;

function startPendingHistoryPolling() {
  if (pendingPollTimer) {
    return;
  }
  const deadline = Date.now() + PENDING_POLL_TIMEOUT_MS;
  pendingPollTimer = setInterval(async () => {
    if (Date.now() >= deadline) {
      stopPendingHistoryPolling();
      return;
    }
    const result = await drainPendingHistory();
    if (result.ok && result.appended.length > 0) {
      stopPendingHistoryPolling();
    }
  }, PENDING_POLL_INTERVAL_MS);
}

function stopPendingHistoryPolling() {
  if (pendingPollTimer) {
    clearInterval(pendingPollTimer);
    pendingPollTimer = null;
  }
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function getHistory() {
  const stored = await chrome.storage.local.get(HISTORY_KEY);
  return Array.isArray(stored[HISTORY_KEY]) ? stored[HISTORY_KEY] : [];
}

async function pushHistory(entry) {
  const history = await getHistory();
  const next = [entry, ...history.filter((item) => item.id !== entry.id)].slice(
    0,
    HISTORY_MAX
  );
  await chrome.storage.local.set({ [HISTORY_KEY]: next });
  return next;
}
