// clipstash popup: helper health, save packet, clipboard history re-copy.

const HELPER_BASE = "http://127.0.0.1:8787";
const PHOTOSHOP_KEY = "clipstashPhotoshop";

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  $("save-packet").addEventListener("click", onSavePacket);
  $("burst-pick").addEventListener("click", onBurstPick);
  $("helper-refresh").addEventListener("click", refreshHealth);
  $("open-options").addEventListener("click", (event) => {
    event.preventDefault();
    chrome.runtime.openOptionsPage();
  });
  $("place-photoshop").addEventListener("change", (event) => {
    chrome.storage.local.set({ [PHOTOSHOP_KEY]: event.target.checked });
  });
  refreshHealth();
  loadHistory();
  loadPhotoshopPref();
});

function send(message) {
  return chrome.runtime.sendMessage(message);
}

async function loadPhotoshopPref() {
  const stored = await chrome.storage.local.get(PHOTOSHOP_KEY);
  $("place-photoshop").checked = Boolean(stored[PHOTOSHOP_KEY]);
}

function placePhotoshopChecked() {
  return Boolean($("place-photoshop").checked);
}

async function refreshHealth() {
  const dot = $("helper-dot");
  const text = $("helper-text");
  const setup = $("helper-setup");
  dot.className = "dot";
  text.textContent = "checking helper…";
  setup.hidden = true;

  const health = await checkHealthDirect();
  if (health && health.ok) {
    dot.className = "dot running";
    text.textContent = `Helper running (v${health.version})`;
  } else {
    dot.className = "dot down";
    text.textContent = "Helper not running";
    setup.hidden = false;
  }
}

async function onSavePacket() {
  const status = $("status");
  const button = $("save-packet");
  button.disabled = true;
  status.hidden = false;
  status.className = "status";
  status.textContent = "saving…";

  const response = await send({ type: "SAVE_PACKET", placePhotoshop: placePhotoshopChecked() });
  button.disabled = false;

  if (response && response.ok) {
    const record = response.packet;
    status.className = "status success";
    status.textContent = `Saved ${record.id}${photoshopSuffix(response.photoshop)}`;
    await copyText(record.clipboard ? record.clipboard.text : `${record.title}\n${record.source_url}`);
    await loadHistory();
  } else {
    status.className = "status error";
    status.textContent = (response && response.error) || "save failed";
  }
}

async function onBurstPick() {
  const status = $("status");
  const button = $("burst-pick");
  button.disabled = true;
  status.hidden = false;
  status.className = "status";
  status.textContent = "capturing burst…";

  const response = await send({ type: "BURST_PICK", placePhotoshop: placePhotoshopChecked() });
  button.disabled = false;

  if (response && response.ok) {
    status.className = "status success";
    status.textContent = `Picker opened (${response.session_id})`;
  } else {
    status.className = "status error";
    status.textContent = (response && response.error) || "burst failed";
  }
}

function photoshopSuffix(result) {
  if (!result) return "";
  return result.ok ? " · Photoshop: placed" : ` · Photoshop: ${result.error || "not placed"}`;
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
  } catch (_error) {
    // Clipboard may be unavailable if the popup lost focus; history still holds it.
  }
}

async function loadHistory() {
  const response = await send({ type: "GET_HISTORY" });
  const history = (response && response.ok && response.history) || [];
  const list = $("history-list");
  list.textContent = "";
  $("history-empty").hidden = history.length > 0;
  $("history-count").textContent = history.length ? `(${history.length})` : "";

  for (const entry of history) {
    const item = document.createElement("li");

    const title = document.createElement("div");
    title.className = "entry-title";
    title.textContent = entry.title || "(untitled)";

    const url = document.createElement("div");
    url.className = "entry-url";
    url.textContent = entry.url || "";

    const actions = document.createElement("div");
    actions.className = "entry-actions";
    actions.append(
      actionButton("Title", () => copyText(entry.title || "")),
      actionButton("URL", () => copyText(entry.url || "")),
      actionButton("Both", () => copyText(entry.text || `${entry.title}\n${entry.url}`))
    );

    item.append(title, url, actions);
    list.append(item);
  }
}

function actionButton(label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

// Unused for now; kept as the direct health-check path for the popup per spec.
async function checkHealthDirect() {
  try {
    const response = await fetch(`${HELPER_BASE}/health`);
    return response.json();
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}
