// clipstash MV3 service worker.
// Talks to the local helper and owns chrome.storage.local clipboard history.

const HELPER_BASE = "http://127.0.0.1:8787";
const HISTORY_KEY = "clipstashHistory";
const HISTORY_MAX = 20;

// 1x1 transparent PNG — placeholder still for the slice-2 "save packet" stub.
const STUB_PNG_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

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
      return saveCurrentPage();
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

async function saveCurrentPage() {
  const tab = await getActiveTab();
  if (!tab || !/^https?:/.test(tab.url || "")) {
    return { ok: false, error: "active tab is not a http(s) page" };
  }

  // Slice 2 stub: no in-page frame yet. Send a placeholder still so the
  // packet schema and helper write path are exercised end to end.
  const payload = {
    title: tab.title || tab.url,
    source_url: tab.url,
    page_url: tab.url,
    site: "generic",
    image_base64: STUB_PNG_DATA_URL.split(",", 2)[1],
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
