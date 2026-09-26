// Site adapters for clipstash.
//
// Interface (PLAN.md §4):
//   {
//     id: string,
//     matches(url: string): boolean,
//     extract(ctx): Promise<{ title, sourceUrl, currentTime? }>,
//     findVideo(ctxOrDocument): HTMLVideoElement | null
//   }
//
// Injected into pages by background.js; exposed as
// `globalThis.ClipStashAdapters` for content.js.

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

  function canonicalYouTubeUrl(url) {
    const u = new URL(url);
    if (u.hostname === "youtu.be") {
      const id = u.pathname.replace(/^\//, "");
      return id ? `https://www.youtube.com/watch?v=${id}` : u.href;
    }
    const videoId = u.searchParams.get("v");
    return videoId ? `https://www.youtube.com/watch?v=${videoId}` : u.href;
  }

  function hostnameMatches(url, hostname) {
    return new URL(url).hostname.replace(/^www\./, "") === hostname;
  }

  const youtube = register({
    id: "youtube",
    matches(url) {
      return hostnameMatches(url, "youtube.com") || hostnameMatches(url, "youtu.be");
    },
    async extract(ctx) {
      const video = this.findVideo(ctx.document) || ctx.video || null;
      const title = (ctx.document.title || ctx.location.href).replace(/\s+-\s+YouTube\s*$/, "");
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

  const generic = register({
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

  return { register, adapt, adapters: registry, ids: { youtube: youtube.id, generic: generic.id } };
})();
