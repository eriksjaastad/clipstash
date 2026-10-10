// clipstash MV3 service worker.
//
// Message hub for the popup/picker and owner of the clipboard history in
// chrome.storage.local. Handles HEALTH, SAVE_PACKET (single frame capture),
// BURST_PICK (burst capture + helper picker), APPEND_HISTORY, GET_HISTORY and
// GET_CAPTURED_URLS (the canonicalized packet URL set used by the
// green-check overlay). Talks to the local helper through lib/helper-api.js.
// Canvas-taint falls back to captureVisibleTab; the content scripts report a
// `cropRect` (video element CSS box × devicePixelRatio) with the taint signal,
// which we forward as `crop_rect` so the helper can crop the full-tab PNG to
// the video rectangle. For tainted *bursts* we first try the helper's native
// ffmpeg burst from the video's media URL (`burst_ffmpeg`); when the media URL
// is missing or the helper path fails, we fall back to a single cropped
// visible-tab shot (`burst_visible_tab`). Burst-chosen history entries are
// drained from the helper's pending queue so picker tabs don't need direct
// storage access.

importScripts("lib/urls.js", "lib/helper-api.js");

const HISTORY_KEY = "clipstashHistory";
const HISTORY_MAX = 20;
const PENDING_POLL_INTERVAL_MS = 1000;
const PENDING_POLL_TIMEOUT_MS = 3 * 60 * 1000;
const CAPTURED_URLS_TTL_MS = 45 * 1000;

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
      return captureAndSave(Boolean(message.placePhotoshop), cleanName(message.name));
    case "BURST_PICK":
      return captureBurstAndOpenPicker(Boolean(message.placePhotoshop), cleanName(message.name));
    case "APPEND_HISTORY":
      return appendHistory(message.packet);
    case "GET_HISTORY":
      await drainPendingHistory();
      return { ok: true, history: await getHistory() };
    case "GET_CAPTURED_URLS":
      return getCapturedUrls();
    default:
      return { ok: false, error: `unknown message type: ${message && message.type}` };
  }
}

async function checkHealth() {
  try {
    const response = await fetch(`${ClipStashHelper.BASE}/health`);
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

function cleanName(name) {
  return typeof name === "string" && name.trim() ? name.trim() : undefined;
}

// -- captured URL index (green-check overlay) --------------------------------

let capturedUrlsCache = { urls: [], fetchedAt: 0 };
let capturedUrlsInFlight = null;
let capturedUrlsGeneration = 0;

async function getCapturedUrls() {
  const now = Date.now();
  if (capturedUrlsCache.fetchedAt && now - capturedUrlsCache.fetchedAt < CAPTURED_URLS_TTL_MS) {
    return { ok: true, urls: capturedUrlsCache.urls };
  }
  if (capturedUrlsInFlight) {
    return capturedUrlsInFlight;
  }
  capturedUrlsInFlight = fetchCapturedUrls().finally(() => {
    capturedUrlsInFlight = null;
  });
  return capturedUrlsInFlight;
}

async function fetchCapturedUrls() {
  const generation = capturedUrlsGeneration;
  let response;
  try {
    response = await fetch(`${ClipStashHelper.BASE}/packets`);
  } catch (_error) {
    // Helper unreachable: the overlay shows nothing and never surfaces errors.
    return { ok: false, urls: [] };
  }
  const body = await response.json().catch(() => ({}));
  const packets = Array.isArray(body.packets) ? body.packets : [];
  const canonicalize =
    (globalThis.ClipStashUrls && globalThis.ClipStashUrls.canonicalizeVideoUrl) ||
    ((url) => url);

  const seen = new Set();
  for (const packet of packets) {
    for (const field of ["source_url", "page_url"]) {
      const value = packet && packet[field];
      if (typeof value !== "string") {
        continue;
      }
      const canonical = canonicalize(value);
      if (canonical) {
        seen.add(canonical);
      }
    }
  }
  const urls = Array.from(seen);
  // A save may have invalidated the cache while this fetch was in flight;
  // don't overwrite the fresher (empty, refetching) state with stale data.
  if (generation === capturedUrlsGeneration) {
    capturedUrlsCache = { urls, fetchedAt: Date.now() };
  }
  return { ok: true, urls };
}

function invalidateCapturedUrls() {
  capturedUrlsGeneration += 1;
  capturedUrlsCache = { urls: [], fetchedAt: 0 };
}

// Injects the capture `files` into the active http(s) tab. Returns
// { tab, captured } with the injected script's result, or { failure } using the
// caller's wording for an injection error and for an empty result.
async function injectCapture(files, injectionError, emptyError) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) {
    return { failure: { ok: false, error: "active tab is not a http(s) page" } };
  }
  let injection;
  try {
    injection = await chrome.scripting.executeScript({ target: { tabId: tab.id }, files });
  } catch (error) {
    return { failure: { ok: false, error: `${injectionError}: ${error}` } };
  }
  const captured = injection && injection[0] && injection[0].result;
  if (!captured) {
    return { failure: { ok: false, error: emptyError } };
  }
  return { tab, captured };
}

// The snake_case video fields every helper POST body starts with, sent as
// captured. `method` is the capture_method label; /bursts/ffmpeg passes none
// and the helper sets it.
function packetFields(captured, method) {
  return {
    title: captured.title,
    source_url: captured.sourceUrl,
    page_url: captured.pageUrl,
    site: captured.site,
    timestamp_sec: captured.timestampSec,
    capture_method: method,
  };
}

// The page_url and site fallbacks the save paths apply. The tainted-burst
// bodies (/bursts/ffmpeg and burst_visible_tab) send them as captured.
function withDefaults(captured) {
  return { ...captured, pageUrl: captured.pageUrl || captured.sourceUrl, site: captured.site || "generic" };
}

async function captureAndSave(placePhotoshop, name) {
  const { tab, captured, failure } = await injectCapture(
    ["lib/urls.js", "lib/adapters.js", "lib/frame.js", "content.js"],
    "injection failed",
    "frame capture failed"
  );
  if (failure) {
    return failure;
  }

  // Canvas draw of a cross-origin <video> can taint the canvas and make
  // toDataURL() throw. The content script reports `tainted: true` with the
  // metadata it did manage to extract, so we can fall back to a visible-tab
  // screenshot (no new permission needed: activeTab, already declared, grants
  // captureVisibleTab when the user invokes the extension). Its `cropRect`
  // (video CSS box × devicePixelRatio) is forwarded as `crop_rect` so the
  // helper crops the full-tab PNG to the video rectangle before saving.
  // Only the visible-tab shot is cropped; a canvas save never sends crop_rect.
  let captureMethod = captured.captureMethod || "canvas";
  let imageDataUrl = captured.imageDataUrl;
  let cropRect;
  if (captured.tainted) {
    captureMethod = "visible_tab";
    cropRect = captured.cropRect;
    try {
      imageDataUrl = await captureVisibleTabPng(tab.windowId);
    } catch (error) {
      return { ok: false, error: `visible-tab fallback failed: ${error}` };
    }
  } else if (!captured.ok) {
    return { ok: false, error: captured.error || "frame capture failed" };
  }

  const payload = {
    ...packetFields(withDefaults(captured), captureMethod),
    image_base64: stripDataUrlPrefix(imageDataUrl || ""),
    photoshop: placePhotoshop,
  };
  if (cropRect) {
    payload.crop_rect = cropRect;
  }
  if (name) {
    payload.name = name;
  }
  return savePacket(payload);
}

async function captureBurstAndOpenPicker(placePhotoshop, name) {
  const { tab, captured, failure } = await injectCapture(
    ["lib/urls.js", "lib/adapters.js", "lib/frame.js", "lib/burst.js"],
    "burst injection failed",
    "burst capture failed"
  );
  if (failure) {
    return failure;
  }

  let fields;
  let frames;

  if (captured.tainted && !captured.ok) {
    // Canvas taint (e.g. googlevideo on YouTube). First try the helper's
    // native ffmpeg burst from the video's media URL (`burst_ffmpeg`); when
    // that is unavailable or fails, fall back to a single visible-tab shot,
    // labeled burst_visible_tab. The content script's `cropRect` is
    // forwarded as `crop_rect` so the helper crops that single frame to the
    // video rectangle.
    const native = await tryNativeBurst(captured, placePhotoshop, name);
    if (native) {
      return native;
    }
    try {
      frames = [await captureVisibleTabPng(tab.windowId)];
    } catch (error) {
      return { ok: false, error: `visible-tab burst fallback failed: ${error}` };
    }
    fields = packetFields(captured, "burst_visible_tab");
  } else if (captured.ok) {
    fields = packetFields(withDefaults(captured), captured.captureMethod || "burst_canvas");
    frames = captured.frames || [];
  } else {
    return { ok: false, error: captured.error || "burst capture failed" };
  }

  if (!frames.length) {
    return { ok: false, error: "burst produced no frames" };
  }

  const payload = { ...fields, frames, photoshop: placePhotoshop };
  if (name) {
    payload.name = name;
  }
  if (captured.cropRect) {
    payload.crop_rect = captured.cropRect;
  }

  const body = await ClipStashHelper.postJson("/bursts", payload);
  if (!body.ok) {
    return body;
  }
  try {
    await openPicker(body.picker_url);
  } catch (error) {
    return { ok: false, error: `opening picker tab failed: ${error}` };
  }
  return { ok: true, session_id: body.session_id, picker_url: body.picker_url };
}

// Try the helper's native ffmpeg burst (`POST /bursts/ffmpeg`) for a tainted
// canvas when the content script reported a media URL. Returns the picker
// result on success, or null when the path is unavailable so the caller can
// fall back to the single visible-tab shot. Never throws.
async function tryNativeBurst(captured, placePhotoshop, name) {
  const mediaUrl = String(captured.mediaUrl || "").trim();
  if (!/^https?:\/\//i.test(mediaUrl)) {
    return null;
  }
  // media_url and timestamp_sec lead this body, ahead of the shared fields.
  const payload = {
    media_url: mediaUrl,
    timestamp_sec: captured.timestampSec,
    ...packetFields(captured),
    photoshop: placePhotoshop,
  };
  if (name) {
    payload.name = name;
  }
  let response;
  try {
    response = await ClipStashHelper.post("/bursts/ffmpeg", payload);
  } catch (_error) {
    // Helper unreachable: fall back without a warning.
    return null;
  }
  const body = await ClipStashHelper.readJson(response);
  if (!body.ok) {
    console.warn("clipstash: native ffmpeg burst unavailable, falling back to visible_tab:", body.error);
    return null;
  }
  try {
    await openPicker(body.picker_url);
  } catch (_error) {
    return null;
  }
  return {
    ok: true,
    session_id: body.session_id,
    picker_url: body.picker_url,
    capture_method: body.capture_method || "burst_ffmpeg",
  };
}

// Opens the helper's picker tab, then polls for the history entry it queues
// when a frame is chosen. Rejects, without polling, if the tab won't open.
async function openPicker(url) {
  await chromeCallback((done) => chrome.tabs.create({ url }, done));
  startPendingHistoryPolling();
}

function captureVisibleTabPng(windowId) {
  return chromeCallback((done) => chrome.tabs.captureVisibleTab(windowId, { format: "png" }, done));
}

// Callback-style wrapper: works on every Chrome MV3 build regardless of
// whether the API's promise form is available.
function chromeCallback(call) {
  return new Promise((resolve, reject) => {
    call((result) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
      } else {
        resolve(result);
      }
    });
  });
}

function stripDataUrlPrefix(value) {
  return (value || "").includes(",") ? value.split(",", 2)[1] : value;
}

async function savePacket(payload) {
  const body = await ClipStashHelper.postJson("/packets", payload);
  if (!body.ok) {
    return body;
  }

  const record = body.packet;
  await pushHistory(historyEntryFromPacket(record));
  invalidateCapturedUrls();
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
  invalidateCapturedUrls();
  return { ok: true };
}

// Fallback path: the helper enqueues burst-chosen history entries while the
// picker tab is open, and we drain them here. This covers the window between
// a chosen frame and the content-script bridge (or a service-worker restart).
async function drainPendingHistory() {
  const body = await ClipStashHelper.fetchJson("/history/pending");
  if (!body.ok) {
    return body;
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
  if (appended.length > 0) {
    invalidateCapturedUrls();
  }
  return { ok: true, appended };
}

// The helper queues entries already in history shape (url, text, createdAt),
// so unlike historyEntryFromPacket this validates rather than maps a record.
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
