// clipstash local helper API: the one place the extension names the helper.
// manifest.json host_permissions must list the same host.
//
// The file is an IIFE that assigns globalThis.ClipStashHelper, so it works in
// the service worker via importScripts, in an extension page via <script>, and
// in the node vm smoke test.

globalThis.ClipStashHelper = (() => {
  const BASE = "http://127.0.0.1:8787";

  // Returns the helper's JSON body when it answers ok, otherwise
  // { ok: false, error }. Never throws.
  async function fetchJson(path, init) {
    let response;
    try {
      response = await fetch(`${BASE}${path}`, init);
    } catch (error) {
      return { ok: false, error: `helper unreachable: ${error}` };
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok || !body.ok) {
      return { ok: false, error: body.error || `helper returned ${response.status}` };
    }
    return body;
  }

  function postJson(path, payload) {
    return fetchJson(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  return { BASE, fetchJson, postJson };
})();
