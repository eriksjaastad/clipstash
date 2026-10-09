// clipstash green-check overlay for creator video grids.
//
// Runs only on YouTube / TikTok / Instagram (manifest content_script matches
// plus the explicit isGreenCheckSite gate below). X / Twitter is deliberately
// OUT OF SCOPE: the gate returns before any observer, badge, or message code
// runs, so x.com / twitter.com pages never get a green-check path.
//
// Matching: grid hrefs (resolved against location.href so relative /watch
// paths work) and packet source_url/page_url are BOTH canonicalized with
// ClipStashUrls.canonicalizeVideoUrl before set lookup, so remakes,
// youtu.be / shorts / reel variants, and query noise hit the same key.
//
// Badge: a small green circle + white check appended to the candidate
// thumbnail anchor (class clipstash-captured-check). The anchor gets
// data-clipstash-captured="1" so rescans never double-badge it. The badge is
// pointer-events: none and never blocks clicks.

(() => {
  const urls = globalThis.ClipStashUrls;
  if (!urls || !urls.isGreenCheckSite(location.href)) {
    return;
  }

  const BADGE_CLASS = "clipstash-captured-check";
  const HOST_CLASS = "clipstash-captured-host";
  const MARK = "data-clipstash-captured";
  const REFRESH_MS = 30 * 1000;
  const SCAN_DEBOUNCE_MS = 250;

  let captured = new Set();
  let scanTimer = null;

  const host = location.hostname.replace(/^www\./, "");
  const isYouTube = host === "youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com");

  function absoluteHref(href) {
    if (!href) {
      return "";
    }
    try {
      return new URL(href, location.href).href;
    } catch (_error) {
      return "";
    }
  }

  function isCandidateHref(href) {
    if (!href) {
      return false;
    }
    if (isYouTube) {
      return href.includes("/watch?v=") || href.includes("/shorts/");
    }
    if (host === "tiktok.com") {
      return /\/@[^/]+\/video\/\d+/.test(href);
    }
    if (host === "instagram.com") {
      return /\/(reel|reels|p)\/[^/?#]+/.test(href);
    }
    return false;
  }

  function candidateLinks() {
    const links = new Set();
    for (const anchor of document.querySelectorAll("a[href]")) {
      if (isCandidateHref(anchor.getAttribute("href") || "")) {
        links.add(anchor);
      }
    }
    return links;
  }

  function applyBadge(link) {
    link.setAttribute(MARK, "1");
    link.classList.add(HOST_CLASS);
    const badge = document.createElement("span");
    badge.className = BADGE_CLASS;
    badge.textContent = "\u2713";
    badge.setAttribute("aria-label", "Already captured with clipstash");
    badge.setAttribute("role", "img");
    link.appendChild(badge);
  }

  function scan() {
    for (const link of candidateLinks()) {
      if (link.getAttribute(MARK) === "1") {
        continue;
      }
      const key = urls.canonicalizeVideoUrl(absoluteHref(link.getAttribute("href") || ""));
      if (key && captured.has(key)) {
        applyBadge(link);
      }
    }
  }

  function scheduleScan() {
    if (scanTimer) {
      return;
    }
    scanTimer = setTimeout(() => {
      scanTimer = null;
      scan();
    }, SCAN_DEBOUNCE_MS);
  }

  async function refresh() {
    let response = null;
    try {
      response = await chrome.runtime.sendMessage({ type: "GET_CAPTURED_URLS" });
    } catch (_error) {
      // Background unavailable (extension reloading, helper down): show
      // nothing rather than surfacing errors to the page.
      captured = new Set();
      return;
    }
    captured = new Set(
      response && response.ok && Array.isArray(response.urls) ? response.urls : []
    );
    scan();
  }

  // SPA grids: rebuild the captured set on navigation/focus, and rescan after
  // DOM mutations (infinite scroll keeps appending thumb cards).
  const observer = new MutationObserver(scheduleScan);
  observer.observe(document.body, { childList: true, subtree: true });

  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refresh();
    }
  });
  window.addEventListener("yt-navigate-finish", refresh);
  setInterval(refresh, REFRESH_MS);

  refresh();
})();
