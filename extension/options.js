// clipstash options page: view and change the helper packet save root.
// Talks to the local helper directly (host_permissions cover 127.0.0.1:8787).

const HELPER_BASE = "http://127.0.0.1:8787";

const currentRoot = document.getElementById("current-root");
const defaultRoot = document.getElementById("default-root");
const configPath = document.getElementById("config-path");
const rootInput = document.getElementById("packet-root");
const statusLine = document.getElementById("status");
const configForm = document.getElementById("config-form");

function setStatus(text, kind) {
  statusLine.textContent = text;
  statusLine.className = `status ${kind}`.trim();
}

async function loadConfig() {
  setStatus("loading…", "");
  try {
    const response = await fetch(`${HELPER_BASE}/config`);
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      setStatus(`helper error: ${payload.error || `HTTP ${response.status}`}`, "error");
      return;
    }
    currentRoot.textContent = payload.packet_root;
    defaultRoot.textContent = payload.default_root;
    configPath.textContent = payload.config_path;
    rootInput.value = payload.packet_root;
    setStatus("helper running", "ok");
  } catch (error) {
    setStatus(`helper unreachable (${HELPER_BASE}): ${error}`, "error");
  }
}

async function saveConfig(event) {
  event.preventDefault();
  const packetRoot = rootInput.value.trim();
  if (!packetRoot) {
    setStatus("validation error: path must not be empty", "error");
    return;
  }
  setStatus("saving…", "");
  try {
    const response = await fetch(`${HELPER_BASE}/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ packet_root: packetRoot }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      setStatus(`validation error: ${payload.error || `HTTP ${response.status}`}`, "error");
      return;
    }
    currentRoot.textContent = payload.packet_root;
    rootInput.value = payload.packet_root;
    setStatus("saved", "ok");
  } catch (error) {
    setStatus(`helper unreachable: ${error}`, "error");
  }
}

configForm.addEventListener("submit", saveConfig);
loadConfig();
