// clipstash MV3 service worker.
// Talks to the local helper and owns chrome.storage.local clipboard history.

const HELPER_BASE = "http://127.0.0.1:8787";
const HISTORY_KEY = "clipstashHistory";
const HISTORY_MAX = 20;

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
      return captureAndSave();
    case "GET_HISTORY":
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

async function captureAndSave() {
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
  if (!captured || !captured.ok) {
    return { ok: false, error: (captured && captured.error) || "frame capture failed" };
  }

  const imageBase64 = (captured.imageDataUrl || "").includes(",")
    ? captured.imageDataUrl.split(",", 2)[1]
    : captured.imageDataUrl;

  const payload = {
    title: captured.title,
    source_url: captured.sourceUrl,
    page_url: captured.pageUrl || captured.sourceUrl,
    site: captured.site || "generic",
    timestamp_sec: captured.timestampSec,
    image_base64: imageBase64,
  };
  return savePacket(payload);
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
  await pushHistory({
    id: record.id,
    title: record.title,
    url: record.source_url,
    text: record.clipboard ? record.clipboard.text : `${record.title}\n${record.source_url}`,
    createdAt: record.created_at,
  });
  return { ok: true, packet: record };
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
