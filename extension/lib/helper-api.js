// clipstash local helper API: the one place the extension names the helper.
// manifest.json host_permissions must list the same host.
//
// The file is an IIFE that assigns globalThis.ClipStashHelper, so it works in
// the service worker via importScripts, in an extension page via <script>, and
// in the node vm smoke test.

globalThis.ClipStashHelper = (() => {
  const BASE = "http://127.0.0.1:8787";

  // fetchJson and postJson return the helper's JSON body when it answers ok,
  // otherwise { ok: false, error }, including when it can't be reached.
  function fetchJson(path, init) {
    return answer(() => fetch(`${BASE}${path}`, init));
  }

  function postJson(path, payload) {
    return answer(() => post(path, payload));
  }

  // POSTs `payload` as JSON. Throws or rejects if the helper can't be reached.
  function post(path, payload) {
    return fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async function answer(request) {
    let response;
    try {
      response = await request();
    } catch (error) {
      return { ok: false, error: `helper unreachable: ${error}` };
    }
    return readJson(response);
  }

  // A helper response's JSON body when it is ok, otherwise { ok: false, error }.
  async function readJson(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok || !body.ok) {
      return { ok: false, error: body.error || `helper returned ${response.status}` };
    }
    return body;
  }

  return { BASE, fetchJson, postJson, post, readJson };
})();
