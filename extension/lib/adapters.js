// Site adapters for clipstash.
//
// Interface:
//   {
//     id: string,
//     matches(url: string): boolean,
//     extract(ctx): Promise<{ title, sourceUrl, currentTime? }>,
//     findVideo(ctxOrDocument): HTMLVideoElement | null
//   }
//
// Injected into pages by background.js; exposed as
// `globalThis.ClipStashAdapters` for lib/frame.js. The first matching adapter
// wins; the generic adapter is the always-matching fallback of last resort.

globalThis.ClipStashAdapters = (() => {
  const registry = [];

  function register(adapter) {
    registry.push(adapter);
    return adapter;
  }

  function adapt(url) {
    let parsed;
    try {
      parsed = new URL(url);
    } catch (_error) {
      parsed = null;
    }
    const adapter =
      registry.find((candidate) => {
        try {
          return candidate.matches(url, parsed);
        } catch (_error) {
          return false;
        }
      }) || registry.find((candidate) => candidate.id === "generic");
    return adapter;
  }

  // -- URL helpers ---------------------------------------------------------

  function hostnameMatches(url, hostname) {
    return new URL(url).hostname.replace(/^www\./, "") === hostname;
  }

  function hostnameMatchesAny(url, hostnames) {
    return hostnames.some((hostname) => hostnameMatches(url, hostname));
  }

  function canonicalYouTubeUrl(url) {
    const u = new URL(url);
    if (u.hostname === "youtu.be") {
      const id = u.pathname.replace(/^\//, "");
      return id ? `https://www.youtube.com/watch?v=${id}` : u.href;
    }
    const videoId = u.searchParams.get("v");
    return videoId ? `https://www.youtube.com/watch?v=${videoId}` : u.href;
  }

  function canonicalTwitterUrl(url) {
    const u = new URL(url);
    const match = u.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/status\/(\d+)/);
    if (match) {
      return `https://x.com/${match[1]}/status/${match[2]}`;
    }
    return urlWithoutQueryHash(u);
  }

  function canonicalInstagramUrl(url) {
    const u = new URL(url);
    const match = u.pathname.match(/^\/(reel|reels|p|tv)\/([\w-]+)/);
    if (match) {
      const type = match[1] === "reels" ? "reel" : match[1];
      return `https://www.instagram.com/${type}/${match[2]}/`;
    }
    return urlWithoutQueryHash(u);
  }

  function canonicalTikTokUrl(url) {
    const u = new URL(url);
    const match = u.pathname.match(/^\/@([\w.-]+)\/video\/(\d+)/);
    if (match) {
      return `https://www.tiktok.com/@${match[1]}/video/${match[2]}`;
    }
    return urlWithoutQueryHash(u);
  }

  function urlWithoutQueryHash(u) {
    const clean = `${u.origin}${u.pathname}`.replace(/\/+$/, "");
    return clean || u.href;
  }

  // -- DOM helpers ---------------------------------------------------------

  function text(selector, document) {
    const node = document.querySelector(selector);
    return node && node.textContent ? node.textContent.trim() : "";
  }

  function metaContent(document, property) {
    const node = document.querySelector(`meta[property="${property}"]`) ||
      document.querySelector(`meta[name="${property}"]`);
    return node ? (node.getAttribute("content") || "").trim() : "";
  }

  function stripSuffix(value, ...suffixes) {
    let out = (value || "").trim();
    for (const suffix of suffixes) {
      if (out.endsWith(suffix)) {
        out = out.slice(0, -suffix.length).trim();
      }
    }
    return out;
  }

  // -- YouTube ---------------------------------------------------------------

  register({
    id: "youtube",
    matches(url) {
      return hostnameMatchesAny(url, ["youtube.com", "youtu.be"]);
    },
    async extract(ctx) {
      const video = this.findVideo(ctx.document) || ctx.video || null;
      const title = stripSuffix(ctx.document.title || "", " - YouTube");
      return {
        title: title || ctx.location.href,
        sourceUrl: canonicalYouTubeUrl(ctx.location.href),
        currentTime: video ? video.currentTime : undefined,
      };
    },
    findVideo(document) {
      return document.querySelector("video.html5-main-video") || document.querySelector("video");
    },
  });

  // -- X / Twitter -----------------------------------------------------------

  register({
    id: "x",
    matches(url) {
      return hostnameMatchesAny(url, ["x.com", "twitter.com"]);
    },
    async extract(ctx) {
      const video = this.findVideo(ctx.document) || ctx.video || null;
      const tweetText = text('article [data-testid="tweetText"]', ctx.document);
      const ogTitle = stripSuffix(metaContent(ctx.document, "og:title"), " / X", " on X");
      const title = stripSuffix(tweetText || ogTitle || ctx.document.title || "", " / X", " on X");
      return {
        title: title || ctx.location.href,
        sourceUrl: canonicalTwitterUrl(ctx.location.href),
        currentTime: video ? video.currentTime : undefined,
      };
    },
    findVideo(document) {
      return document.querySelector("article video") || document.querySelector("video");
    },
  });

  // -- Instagram -------------------------------------------------------------

  register({
    id: "instagram",
    matches(url) {
      return hostnameMatches(url, "instagram.com");
    },
    async extract(ctx) {
      const video = this.findVideo(ctx.document) || ctx.video || null;
      const title = stripSuffix(
        metaContent(ctx.document, "og:title") || ctx.document.title || "",
        " on Instagram",
        " - Instagram"
      );
      return {
        title: title || ctx.location.href,
        sourceUrl: canonicalInstagramUrl(ctx.location.href),
        currentTime: video ? video.currentTime : undefined,
      };
    },
    findVideo(document) {
      return document.querySelector("video");
    },
  });

  // -- TikTok ----------------------------------------------------------------

  register({
    id: "tiktok",
    matches(url) {
      return hostnameMatches(url, "tiktok.com");
    },
    async extract(ctx) {
      const video = this.findVideo(ctx.document) || ctx.video || null;
      const title = stripSuffix(
        metaContent(ctx.document, "og:title") || ctx.document.title || "",
        " | TikTok",
        " - TikTok",
        " on TikTok"
      );
      return {
        title: title || ctx.location.href,
        sourceUrl: canonicalTikTokUrl(ctx.location.href),
        currentTime: video ? video.currentTime : undefined,
      };
    },
    findVideo(document) {
      return document.querySelector("video");
    },
  });

  // -- Generic fallback ------------------------------------------------------

  register({
    id: "generic",
    matches() {
      return true;
    },
    async extract(ctx) {
      const video = this.findVideo(ctx.document) || ctx.video || null;
      return {
        title: ctx.document.title || ctx.location.href,
        sourceUrl: ctx.location.href,
        currentTime: video ? video.currentTime : undefined,
      };
    },
    findVideo(document) {
      return document.querySelector("video");
    },
  });

  return { adapt };
})();
