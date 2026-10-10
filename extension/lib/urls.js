// clipstash URL canonicalization shared by the capture adapters, the service
// worker captured-URL index, and the green-check overlay.
//
// Matching rule: canonicalize BOTH the grid href AND each packet's
// source_url + page_url before set membership, so remakes of the same video
// share one canonical key.
//
// Rules:
// - YouTube: youtu.be/<id>, www/m.youtube.com/watch?v=<id> → https://www.youtube.com/watch?v=<id>.
//   /shorts/<id> → the same watch URL. Other YouTube paths drop query/hash.
//   Relative hrefs must be absolutized by the caller (overlay uses location.href as base).
// - Instagram: /reel|/reels|/p|/tv/<code>/ → https://www.instagram.com/<reel|p|tv>/<code>/
//   (/reels/ collapses to /reel/).
// - TikTok: /@user/video/<id> → https://www.tiktok.com/@user/video/<id>
// - X/Twitter: /<user>/status/<id> → https://x.com/<user>/status/<id> for capture
//   only. The green-check overlay MUST NOT use X (see isGreenCheckSite).
// - Anything else: query/hash noise is stripped (origin + pathname, trailing
//   slashes removed).
// - Invalid / non-http(s) URLs return "" — callers ignore empty keys.
//
// The file is an IIFE that assigns globalThis.ClipStashUrls, so it works in a
// page content script, in a service worker via importScripts, and in the node
// vm smoke test.

globalThis.ClipStashUrls = (() => {
  function parse(url) {
    if (typeof url !== "string" || !url.trim()) {
      return null;
    }
    try {
      const parsed = new URL(url.trim());
      return /^https?:$/.test(parsed.protocol) ? parsed : null;
    } catch (_error) {
      return null;
    }
  }

  function bareHostname(parsed) {
    return parsed.hostname.replace(/^www\./, "");
  }

  function stripQueryHash(parsed) {
    const clean = `${parsed.origin}${parsed.pathname}`.replace(/\/+$/, "");
    return clean || parsed.href;
  }

  function canonicalizeVideoUrl(url) {
    const parsed = parse(url);
    if (!parsed) {
      return "";
    }

    const host = bareHostname(parsed);

    // www / m / music.youtube.com and youtu.be all collapse to the same watch key.
    if (host === "youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com")) {
      if (host === "youtu.be") {
        const id = parsed.pathname.split("/").filter(Boolean)[0] || "";
        return id ? `https://www.youtube.com/watch?v=${id}` : stripQueryHash(parsed);
      }
      const videoId = parsed.searchParams.get("v");
      if (parsed.pathname === "/watch" && videoId) {
        return `https://www.youtube.com/watch?v=${videoId}`;
      }
      const shorts = parsed.pathname.match(/^\/shorts\/([^/?#]+)/);
      if (shorts) {
        return `https://www.youtube.com/watch?v=${shorts[1]}`;
      }
      return stripQueryHash(parsed);
    }

    if (host === "instagram.com") {
      const match = parsed.pathname.match(/^\/(reel|reels|p|tv)\/([^/?#]+)/);
      if (match) {
        const type = match[1] === "reels" ? "reel" : match[1];
        return `https://www.instagram.com/${type}/${match[2]}/`;
      }
      return stripQueryHash(parsed);
    }

    if (host === "tiktok.com") {
      const match = parsed.pathname.match(/^\/@([\w.-]+)\/video\/(\d+)/);
      if (match) {
        return `https://www.tiktok.com/@${match[1]}/video/${match[2]}`;
      }
      return stripQueryHash(parsed);
    }

    if (host === "x.com" || host === "twitter.com") {
      const match = parsed.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/status\/(\d+)/);
      if (match) {
        return `https://x.com/${match[1]}/status/${match[2]}`;
      }
      return stripQueryHash(parsed);
    }

    return stripQueryHash(parsed);
  }

  function isGreenCheckSite(url) {
    const parsed = parse(url);
    if (!parsed) {
      return false;
    }
    const host = bareHostname(parsed);
    return (
      host === "youtu.be" ||
      host === "youtube.com" ||
      host.endsWith(".youtube.com") ||
      host === "tiktok.com" ||
      host === "instagram.com"
    );
  }

  return {
    canonicalizeVideoUrl,
    isGreenCheckSite,
  };
})();
