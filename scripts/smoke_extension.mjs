// Dependency-free smoke test for the extension JS, runnable without Chrome.
//
// Usage: node scripts/smoke_extension.mjs
//
// Covers:
//   - adapter selection for YouTube / X / Instagram / TikTok / generic
//   - title + canonical source_url extraction via the adapter interface
//   - content.js canvas path (success) and taint path (visible_tab fallback
//     signal returned to background.js, including cropRect)
//   - burst.js canvas stepping (success), taint path (burst_visible_tab
//     fallback signal, including cropRect and the video's mediaUrl), and
//     non-seekable single-frame path
//   - urls.js canonicalizeVideoUrl + isGreenCheckSite (remake matching;
//     X canonicalize exists for capture but is never a green-check site)
//   - captured-overlay.js badge application on a YT grid and the X no-op gate
//   - background.js SAVE_PACKET / BURST_PICK canvas, visible_tab and ffmpeg fallbacks
//
// Stubs the minimal DOM surface the scripts touch; no browser needed.

import { readFileSync } from "node:fs";
import { createContext, runInNewContext } from "node:vm";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const adaptersSrc = readFileSync(join(root, "extension/lib/adapters.js"), "utf8");
const urlsSrc = readFileSync(join(root, "extension/lib/urls.js"), "utf8");
const capturedOverlaySrc = readFileSync(join(root, "extension/lib/captured-overlay.js"), "utf8");
const contentSrc = readFileSync(join(root, "extension/content.js"), "utf8");
const burstSrc = readFileSync(join(root, "extension/lib/burst.js"), "utf8");
const pickerBridgeSrc = readFileSync(join(root, "extension/lib/picker-bridge.js"), "utf8");
const backgroundSrc = readFileSync(join(root, "extension/background.js"), "utf8");

let failures = 0;
function check(condition, label) {
  if (condition) {
    console.log(`ok   ${label}`);
  } else {
    failures += 1;
    console.error(`FAIL ${label}`);
  }
}

function makeVideo() {
  return {
    videoWidth: 1280,
    videoHeight: 720,
    currentTime: 42.5,
    getBoundingClientRect: () => ({ left: 8, top: 120, width: 640, height: 360 }),
  };
}

// Minimal querySelector supporting the selectors used by the adapters.
function makeDocument({ title, ogTitle, tweetText, video }) {
  const nodes = new Map([
    ["video", video || null],
    ["article video", video || null],
    ["video.html5-main-video", video || null],
    ['article [data-testid="tweetText"]', tweetText ? { textContent: tweetText } : null],
    ['meta[property="og:title"]', ogTitle ? { getAttribute: () => ogTitle } : null],
  ]);
  return {
    title,
    querySelector(selector) {
      return nodes.get(selector) ?? null;
    },
  };
}

function makeContext(url, doc) {
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    Date,
    Promise,
    URL,
    location: { href: url },
    document: doc,
    window: { devicePixelRatio: 2 },
  };
  createContext(sandbox);
  return sandbox;
}

function loadAdapters(sandbox) {
  // The script's final expression is the assignment to globalThis.ClipStashAdapters,
  // so runInNewContext returns the adapter registry object directly.
  return runInNewContext(adaptersSrc, sandbox, { filename: "adapters.js" });
}

function loadUrls(sandbox) {
  return runInNewContext(urlsSrc, sandbox, { filename: "urls.js" });
}

// -- adapter selection -----------------------------------------------------

{
  const sandbox = makeContext("https://www.youtube.com/watch?v=dQw4w9WgXcQ", makeDocument({}));
  const adapters = loadAdapters(sandbox);
  const cases = [
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"],
    ["https://youtu.be/dQw4w9WgXcQ", "youtube"],
    ["https://x.com/someone/status/1234567890123456789", "x"],
    ["https://twitter.com/someone/status/1234567890123456789", "x"],
    ["https://www.instagram.com/reel/CxYz123/", "instagram"],
    ["https://www.instagram.com/p/CxYz123/", "instagram"],
    ["https://www.tiktok.com/@someone/video/7300000000000000000", "tiktok"],
    ["https://example.com/watch?v=1", "generic"],
  ];
  for (const [url, expected] of cases) {
    check(adapters.adapt(url).id === expected, `adapt ${url} -> ${expected}`);
  }
}

// -- adapter extraction ----------------------------------------------------

async function extractFor(url, doc) {
  const sandbox = makeContext(url, doc);
  const adapters = loadAdapters(sandbox);
  const adapter = adapters.adapt(url);
  return adapter.extract({ document: doc, location: { href: url }, video: doc.querySelector("video") || undefined });
}

{
  const video = makeVideo();
  const doc = makeDocument({ title: "How I edit thumbnails - YouTube", video });
  const info = await extractFor("https://youtu.be/dQw4w9WgXcQ", doc);
  check(info.sourceUrl === "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube canonical source_url");
  check(info.title === "How I edit thumbnails", "youtube title strips - YouTube");
  check(info.currentTime === 42.5, "youtube currentTime");
}

{
  const video = makeVideo();
  const doc = makeDocument({
    title: "Post by someone on X",
    tweetText: "This is the tweet text / X",
    video,
  });
  const info = await extractFor("https://x.com/someone/status/1234567890123456789?s=20", doc);
  check(info.sourceUrl === "https://x.com/someone/status/1234567890123456789", "x canonical source_url");
  check(info.title === "This is the tweet text", "x title prefers tweet text");
  check(info.currentTime === 42.5, "x currentTime");
}

{
  const video = makeVideo();
  const doc = makeDocument({ title: "Fallback page title", ogTitle: "Reel by creator on Instagram", video });
  const info = await extractFor("https://www.instagram.com/reel/CxYz123/?utm_source=x", doc);
  check(info.sourceUrl === "https://www.instagram.com/reel/CxYz123/", "instagram canonical source_url");
  check(info.title === "Reel by creator", "instagram title from og:title");
  check(info.currentTime === 42.5, "instagram currentTime");
}

{
  const video = makeVideo();
  const doc = makeDocument({ title: "Fallback page title", ogTitle: "someone on TikTok", video });
  const info = await extractFor("https://www.tiktok.com/@someone/video/7300000000000000000", doc);
  check(info.sourceUrl === "https://www.tiktok.com/@someone/video/7300000000000000000", "tiktok canonical source_url");
  check(info.title === "someone", "tiktok title from og:title");
  check(info.currentTime === 42.5, "tiktok currentTime");
}

{
  const video = makeVideo();
  const doc = makeDocument({ title: "Generic page", video });
  const info = await extractFor("https://example.com/watch?v=1", doc);
  check(info.title === "Generic page", "generic title from document.title");
  check(info.sourceUrl === "https://example.com/watch?v=1", "generic source_url is page URL");
}

// -- urls.js: canonicalizeVideoUrl + isGreenCheckSite (#7691) -----------------

{
  const sandbox = makeContext("https://www.youtube.com/watch?v=dQw4w9WgXcQ", makeDocument({}));
  const urls = loadUrls(sandbox);
  const canonicalize = (value) => urls.canonicalizeVideoUrl(value);

  const cases = [
    // [input, expected]
    ["https://youtu.be/dQw4w9WgXcQ?si=abc", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    ["https://www.youtube.com/shorts/abc123?feature=share", "https://www.youtube.com/watch?v=abc123"],
    ["https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    ["https://www.instagram.com/reel/CxYz123/?utm_source=x", "https://www.instagram.com/reel/CxYz123/"],
    ["https://www.instagram.com/reels/CxYz123/", "https://www.instagram.com/reel/CxYz123/"],
    ["https://www.instagram.com/p/CxYz123/", "https://www.instagram.com/p/CxYz123/"],
    ["https://www.instagram.com/tv/CxYz123/", "https://www.instagram.com/tv/CxYz123/"],
    ["https://www.tiktok.com/@someone/video/7300000000000000000?is_from_webapp=1", "https://www.tiktok.com/@someone/video/7300000000000000000"],
    ["https://x.com/someone/status/1234567890123456789?s=20", "https://x.com/someone/status/1234567890123456789"],
    ["https://twitter.com/someone/status/1234567890123456789", "https://x.com/someone/status/1234567890123456789"],
    ["https://example.com/watch?v=1&x=2", "https://example.com/watch"],
    ["not a url", ""],
    ["ftp://example.com/file", ""],
  ];
  for (const [input, expected] of cases) {
    check(canonicalize(input) === expected, `canonicalizeVideoUrl ${input} -> ${expected}`);
  }

  // Green-check site gate: YT/TikTok/IG yes, X/Twitter no.
  const greenSites = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/@creator/videos",
    "https://www.tiktok.com/@someone/video/7300000000000000000",
    "https://www.instagram.com/reel/CxYz123/",
  ];
  for (const url of greenSites) {
    check(urls.isGreenCheckSite(url) === true, `isGreenCheckSite true for ${url}`);
  }
  check(urls.isGreenCheckSite("https://x.com/someone/status/1234567890123456789") === false, "isGreenCheckSite false for x.com");
  check(urls.isGreenCheckSite("https://twitter.com/someone/status/1234567890123456789") === false, "isGreenCheckSite false for twitter.com");
  check(urls.isGreenCheckSite("https://example.com/watch?v=1") === false, "isGreenCheckSite false for non-green-check site");
}

{
  // Matching: packet source_url + page_url variants share one canonical key
  // with the grid href (remakes / shorts / query junk all collide).
  const sandbox = makeContext("https://www.youtube.com/watch?v=dQw4w9WgXcQ", makeDocument({}));
  const urls = loadUrls(sandbox);
  const packets = [
    { source_url: "https://youtu.be/dQw4w9WgXcQ", page_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1" },
    { source_url: "https://www.tiktok.com/@someone/video/7300000000000000000", page_url: "https://www.tiktok.com/@someone/video/7300000000000000000" },
    { source_url: "https://www.instagram.com/reels/CxYz123/", page_url: "https://www.instagram.com/reel/CxYz123/?utm_source=x" },
  ];
  const captured = new Set();
  for (const packet of packets) {
    for (const field of ["source_url", "page_url"]) {
      const key = urls.canonicalizeVideoUrl(packet[field]);
      if (key) captured.add(key);
    }
  }
  check(captured.has("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "packet youtu.be/watch variants collapse to one YT key");
  check(captured.has("https://www.tiktok.com/@someone/video/7300000000000000000"), "packet TikTok URL collapses to one key");
  check(captured.has("https://www.instagram.com/reel/CxYz123/"), "packet reels/reel variants collapse to one IG key");
  check(
    captured.has(urls.canonicalizeVideoUrl("https://www.youtube.com/shorts/dQw4w9WgXcQ")),
    "grid shorts href hits the same captured YT key"
  );
  check(
    captured.has(urls.canonicalizeVideoUrl("https://www.instagram.com/reel/CxYz123/?utm_source=grid")),
    "grid reel href hits the same captured IG key"
  );
}

{
  // adapters.js exposes the shared canonicalize + site gate when urls.js is loaded.
  const sandbox = makeContext("https://www.youtube.com/watch?v=dQw4w9WgXcQ", makeDocument({}));
  loadUrls(sandbox);
  const adapters = loadAdapters(sandbox);
  check(
    adapters.canonicalizeVideoUrl("https://youtu.be/dQw4w9WgXcQ") === "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "ClipStashAdapters.canonicalizeVideoUrl delegates to ClipStashUrls"
  );
  check(adapters.isGreenCheckSite("https://www.tiktok.com/@a/video/1") === true, "ClipStashAdapters.isGreenCheckSite true for TikTok");
  check(adapters.isGreenCheckSite("https://x.com/a/status/1") === false, "ClipStashAdapters.isGreenCheckSite false for X");
}

// -- captured-overlay.js: badge application + X no-op -------------------------

function makeFakeAnchor(href) {
  const classes = new Set();
  const children = [];
  const attrs = { href };
  return {
    href,
    children,
    classList: {
      add(name) {
        classes.add(name);
      },
      contains(name) {
        return classes.has(name);
      },
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
    },
    setAttribute(name, value) {
      attrs[name] = String(value);
    },
    appendChild(node) {
      children.push(node);
    },
  };
}

function makeOverlayDocument(anchors) {
  return {
    body: {},
    visibilityState: "visible",
    addEventListener() {},
    querySelectorAll(selector) {
      return selector === "a[href]" ? anchors : [];
    },
    createElement(tag) {
      if (tag !== "span") throw new Error(`unexpected createElement(${tag})`);
      return makeFakeAnchor("");
    },
  };
}

{
  const capturedAnchor = makeFakeAnchor("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  const notCapturedAnchor = makeFakeAnchor("https://www.youtube.com/watch?v=zzzzzzzzzzz");
  const doc = makeOverlayDocument([capturedAnchor, notCapturedAnchor]);
  const sentMessages = [];
  let observerCount = 0;
  class FakeMutationObserver {
    constructor(callback) {
      this.callback = callback;
      observerCount += 1;
    }
    observe() {}
    disconnect() {}
  }
  const sandbox = {
    console,
    Promise,
    Set,
    URL,
    setTimeout,
    setInterval: () => 0,
    location: { href: "https://www.youtube.com/results?search_query=banners", hostname: "www.youtube.com" },
    document: doc,
    window: { addEventListener() {} },
    chrome: {
      runtime: {
        sendMessage: async (message) => {
          sentMessages.push(message);
          return { ok: true, urls: ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] };
        },
      },
    },
    MutationObserver: FakeMutationObserver,
  };
  loadUrls(sandbox);
  runInNewContext(capturedOverlaySrc, sandbox, { filename: "captured-overlay.js" });
  await new Promise((resolve) => setTimeout(resolve, 0));

  check(observerCount === 1, "overlay installs one MutationObserver on a green-check site");
  check(
    sentMessages.length >= 1 && sentMessages[0].type === "GET_CAPTURED_URLS",
    "overlay asks background for GET_CAPTURED_URLS"
  );
  check(capturedAnchor.getAttribute("data-clipstash-captured") === "1", "overlay marks the captured anchor");
  check(
    capturedAnchor.children.length === 1 && capturedAnchor.children[0].className === "clipstash-captured-check",
    "overlay appends the clipstash-captured-check badge"
  );
  check(notCapturedAnchor.getAttribute("data-clipstash-captured") === null, "overlay leaves non-captured anchors unmarked");
  check(notCapturedAnchor.children.length === 0, "overlay adds no badge to non-captured anchors");
}

{
  // Relative grid hrefs (common on YouTube) must resolve against location.href
  // before canonicalize, otherwise badges never match absolute packet URLs.
  const capturedAnchor = makeFakeAnchor("/watch?v=dQw4w9WgXcQ");
  const notCapturedAnchor = makeFakeAnchor("/watch?v=zzzzzzzzzzz");
  const doc = makeOverlayDocument([capturedAnchor, notCapturedAnchor]);
  const sentMessages = [];
  class FakeMutationObserver {
    constructor() {}
    observe() {}
    disconnect() {}
  }
  const sandbox = {
    console,
    Promise,
    Set,
    URL,
    setTimeout,
    setInterval: () => 0,
    location: { href: "https://www.youtube.com/@creator/videos", hostname: "www.youtube.com" },
    document: doc,
    window: { addEventListener() {} },
    chrome: {
      runtime: {
        sendMessage: async (message) => {
          sentMessages.push(message);
          return { ok: true, urls: ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] };
        },
      },
    },
    MutationObserver: FakeMutationObserver,
  };
  loadUrls(sandbox);
  runInNewContext(capturedOverlaySrc, sandbox, { filename: "captured-overlay.js" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  check(
    capturedAnchor.getAttribute("data-clipstash-captured") === "1",
    "overlay badges relative /watch?v= hrefs against absolute packet URLs"
  );
  check(notCapturedAnchor.getAttribute("data-clipstash-captured") === null, "overlay leaves unmatched relative hrefs unmarked");
}

{
  // X / Twitter is out of scope: the overlay must no-op before touching the
  // DOM, observers, or chrome messaging. The sandbox deliberately omits
  // document / MutationObserver / setInterval, so any attempt to reach them
  // throws and fails the test.
  const sentMessages = [];
  const sandbox = {
    console,
    Promise,
    Set,
    URL,
    location: { href: "https://x.com/someone/status/1234567890123456789", hostname: "x.com" },
    chrome: {
      runtime: {
        sendMessage: async (message) => {
          sentMessages.push(message);
          return { ok: true, urls: [] };
        },
      },
    },
  };
  loadUrls(sandbox);
  let threw = false;
  try {
    runInNewContext(capturedOverlaySrc, sandbox, { filename: "captured-overlay.js" });
  } catch (_error) {
    threw = true;
  }
  check(threw === false, "overlay no-ops cleanly on x.com");
  check(sentMessages.length === 0, "overlay never messages background on x.com");
}

// -- content.js: canvas success and taint fallback ---------------------------

function makeCanvasDoc(video, { taint } = {}) {
  const nodes = new Map([
    ["video", video],
    ["video.html5-main-video", null],
    ["article video", null],
  ]);
  const document = {
    title: "Canvas test - YouTube",
    querySelector(selector) {
      return nodes.get(selector) ?? null;
    },
    createElement(tag) {
      if (tag !== "canvas") throw new Error(`unexpected createElement(${tag})`);
      const canvas = {
        width: 0,
        height: 0,
        getContext() {
          return {
            drawImage() {
              if (taint) {
                const error = new Error("The canvas has been tainted by cross-origin data.");
                error.name = "SecurityError";
                throw error;
              }
            },
          };
        },
        toDataURL() {
          if (taint) throw new Error("Tainted canvases may not be exported.");
          return "data:image/png;base64,AAAA";
        },
      };
      return canvas;
    },
  };
  return document;
}

async function runContent(url, doc) {
  const sandbox = makeContext(url, doc);
  loadAdapters(sandbox);
  return runInNewContext(contentSrc, sandbox, { filename: "content.js" });
}

{
  const result = await runContent("https://www.youtube.com/watch?v=dQw4w9WgXcQ", makeCanvasDoc(makeVideo()));
  check(result.ok === true, "content.js canvas path succeeds");
  check(result.captureMethod === "canvas", "content.js labels canvas capture");
  check(result.site === "youtube", "content.js uses youtube adapter");
  check(result.imageDataUrl === "data:image/png;base64,AAAA", "content.js returns image data URL");
}

{
  const result = await runContent("https://www.youtube.com/watch?v=dQw4w9WgXcQ", makeCanvasDoc(makeVideo(), { taint: true }));
  check(result.ok === false && result.tainted === true, "content.js reports tainted canvas");
  check(result.captureMethod === "visible_tab", "content.js signals visible_tab fallback");
  check(result.title === "Canvas test", "content.js keeps metadata on taint");
  check(result.sourceUrl === "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "content.js keeps source_url on taint");
  check(
    result.cropRect &&
      result.cropRect.x === 8 &&
      result.cropRect.y === 120 &&
      result.cropRect.width === 640 &&
      result.cropRect.height === 360 &&
      result.cropRect.dpr === 2,
    "content.js taint signal includes cropRect with x/y/width/height/dpr"
  );
}

// -- burst.js: canvas stepping, taint fallback, non-seekable -------------------

function makeBurstVideo({ seekable = true, center = 10, paused = true } = {}) {
  const listeners = new Map();
  let time = center;
  const video = {
    videoWidth: 1280,
    videoHeight: 720,
    paused,
    currentSrc: "https://r1---sn-abc.googlevideo.com/videoplayback?expire=123",
    src: "https://r1---sn-abc.googlevideo.com/videoplayback?expire=123",
    seekable: seekable
      ? { length: 1, start: () => 0, end: () => 300 }
      : { length: 0, start: () => 0, end: () => 0 },
    getBoundingClientRect: () => ({ left: 8, top: 120, width: 640, height: 360 }),
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(fn);
    },
    removeEventListener(name, fn) {
      listeners.set(name, (listeners.get(name) || []).filter((f) => f !== fn));
    },
    pause() {
      this.paused = true;
    },
    play() {
      this.paused = false;
    },
  };
  Object.defineProperty(video, "currentTime", {
    get() {
      return time;
    },
    set(value) {
      time = value;
      setTimeout(() => (listeners.get("seeked") || []).slice().forEach((fn) => fn()), 0);
    },
  });
  return video;
}

function makeBurstDocument(video, { taint = false, throwNonTaint = false } = {}) {
  const nodes = new Map([
    ["video", video],
    ["video.html5-main-video", null],
    ["article video", null],
  ]);
  const drawnTimes = [];
  return {
    title: "Burst test - YouTube",
    drawnTimes,
    querySelector(selector) {
      return nodes.get(selector) ?? null;
    },
    createElement(tag) {
      if (tag !== "canvas") throw new Error(`unexpected createElement(${tag})`);
      return {
        width: 0,
        height: 0,
        getContext() {
          return {
            drawImage() {
              if (taint) {
                const error = new Error("The canvas has been tainted by cross-origin data.");
                error.name = "SecurityError";
                throw error;
              }
              if (throwNonTaint) {
                throw new Error("InvalidStateError: the frame is not available");
              }
              drawnTimes.push(video.currentTime);
            },
          };
        },
        toDataURL() {
          if (taint) throw new Error("Tainted canvases may not be exported.");
          return "data:image/png;base64,AAAA";
        },
      };
    },
  };
}

async function runBurst(url, doc) {
  const sandbox = makeContext(url, doc);
  loadAdapters(sandbox);
  return runInNewContext(burstSrc, sandbox, { filename: "burst.js" });
}

{
  const video = makeBurstVideo();
  const doc = makeBurstDocument(video);
  const result = await runBurst("https://www.youtube.com/watch?v=dQw4w9WgXcQ", doc);
  check(result.ok === true, "burst.js canvas stepping succeeds");
  check(result.captureMethod === "burst_canvas", "burst.js labels burst_canvas");
  check(result.site === "youtube", "burst.js uses youtube adapter");
  check(result.frames.length === 15, "burst.js captures 2*N+1 frames (15)");
  check(doc.drawnTimes.length === 15, "burst.js draws every target frame");
  check(Math.abs(doc.drawnTimes[0] - 8.95) < 1e-9, "burst.js first target is center - 7*0.15");
  check(Math.abs(doc.drawnTimes[14] - 11.05) < 1e-9, "burst.js last target is center + 7*0.15");
  check(Math.abs(video.currentTime - 10) < 1e-9, "burst.js restores original currentTime");
}

{
  const video = makeBurstVideo();
  const doc = makeBurstDocument(video, { taint: true });
  const result = await runBurst("https://www.youtube.com/watch?v=dQw4w9WgXcQ", doc);
  check(result.ok === false && result.tainted === true, "burst.js reports taint");
  check(result.captureMethod === "burst_visible_tab", "burst.js signals burst_visible_tab fallback");
  check(result.title === "Burst test", "burst.js keeps metadata on taint");
  check(result.sourceUrl === "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "burst.js keeps source_url on taint");
  check(
    result.mediaUrl === "https://r1---sn-abc.googlevideo.com/videoplayback?expire=123",
    "burst.js taint signal includes mediaUrl from video.currentSrc"
  );
  check(
    result.cropRect &&
      result.cropRect.x === 8 &&
      result.cropRect.y === 120 &&
      result.cropRect.width === 640 &&
      result.cropRect.height === 360 &&
      result.cropRect.dpr === 2,
    "burst.js taint signal includes cropRect with x/y/width/height/dpr"
  );
}

{
  const video = makeBurstVideo({ seekable: false });
  const doc = makeBurstDocument(video);
  const result = await runBurst("https://example.com/watch?v=1", doc);
  check(result.ok === true, "burst.js non-seekable path succeeds");
  check(result.frames.length === 1, "burst.js non-seekable captures single frame");
  check(result.captureMethod === "burst_canvas", "burst.js non-seekable labels burst_canvas");
}

{
  // Near a seekable-range boundary, targets are clamped and duplicates dropped.
  const video = makeBurstVideo({ center: 0.5 });
  const doc = makeBurstDocument(video);
  const result = await runBurst("https://www.youtube.com/watch?v=dQw4w9WgXcQ", doc);
  check(result.ok === true, "burst.js boundary-clamped path succeeds");
  check(result.frames.length === 12, "burst.js clamps targets to seekable range (12 unique)");
  check(doc.drawnTimes[0] === 0, "burst.js clamps first target to seekable start");
  check(Math.abs(doc.drawnTimes[11] - 1.55) < 1e-9, "burst.js keeps last clamped target");
  check(Math.abs(video.currentTime - 0.5) < 1e-9, "burst.js restores center after clamping");
}

{
  // A non-taint capture error must still restore playback state.
  const video = makeBurstVideo({ paused: false });
  const doc = makeBurstDocument(video, { throwNonTaint: true });
  const result = await runBurst("https://www.youtube.com/watch?v=dQw4w9WgXcQ", doc);
  check(result.ok === false, "burst.js reports non-taint capture errors");
  check(video.paused === false, "burst.js restores playing state after capture error");
  check(Math.abs(video.currentTime - 10) < 1e-9, "burst.js restores currentTime after capture error");
}

// -- picker-bridge.js: forwards clipstash:chosen to the service worker ---------

{
  const listeners = new Map();
  const messages = [];
  const sandbox = {
    console,
    window: {
      addEventListener(name, fn) {
        listeners.set(name, fn);
      },
      dispatchEvent(event) {
        const fn = listeners.get(event.type);
        if (fn) fn(event);
        return true;
      },
    },
    chrome: {
      runtime: {
        sendMessage(message) {
          messages.push(message);
        },
      },
    },
  };
  createContext(sandbox);
  runInNewContext(pickerBridgeSrc, sandbox, { filename: "picker-bridge.js" });
  check(listeners.has("clipstash:chosen"), "picker-bridge registers clipstash:chosen listener");

  const packet = {
    id: "01JBRIDGE0000000000000000",
    title: "Bridge packet",
    source_url: "https://example.com/v=bridge",
    created_at: "2026-09-26T12:00:00+00:00",
  };
  sandbox.window.dispatchEvent({ type: "clipstash:chosen", detail: packet });
  check(messages.length === 1, "picker-bridge sends one message per chosen event");
  check(
    messages[0] && messages[0].type === "APPEND_HISTORY" && messages[0].packet === packet,
    "picker-bridge forwards packet as APPEND_HISTORY"
  );

  sandbox.window.dispatchEvent({ type: "clipstash:chosen", detail: null });
  check(messages.length === 1, "picker-bridge ignores chosen events without a packet");
}

// -- options.js: loads config, saves new root via PUT /config ------------------

{
  const optionsSrc = readFileSync(join(root, "extension/options.js"), "utf8");
  const elements = {
    "current-root": { textContent: "" },
    "default-root": { textContent: "" },
    "config-path": { textContent: "" },
    "packet-root": { value: "" },
    status: { textContent: "", className: "" },
    "config-form": {
      listeners: {},
      addEventListener(name, fn) {
        this.listeners[name] = fn;
      },
    },
  };
  const fetched = [];
  const sandbox = {
    console,
    document: {
      getElementById(id) {
        return elements[id] ?? null;
      },
    },
    fetch: async (url, options = {}) => {
      fetched.push({ url, options });
      if (options.method === "PUT") {
        const body = JSON.parse(options.body);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            packet_root: `/resolved/${body.packet_root}`,
            default_root: "/home/clipstash/Clipstash/packets",
            config_path: "/home/clipstash/Clipstash/config.json",
          }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ok: true,
          packet_root: "/home/clipstash/Clipstash/packets",
          default_root: "/home/clipstash/Clipstash/packets",
          config_path: "/home/clipstash/Clipstash/config.json",
        }),
      };
    },
  };
  createContext(sandbox);
  runInNewContext(optionsSrc, sandbox, { filename: "options.js" });
  await new Promise((resolve) => setTimeout(resolve, 0));

  check(fetched.length >= 1 && fetched[0].url.endsWith("/config"), "options.js fetches /config on load");
  check(
    elements["current-root"].textContent === "/home/clipstash/Clipstash/packets",
    "options.js renders the current root"
  );
  check(typeof elements["config-form"].listeners.submit === "function", "options.js registers a save handler");

  elements["packet-root"].value = "~/new-packets";
  await elements["config-form"].listeners.submit({ preventDefault() {} });

  const put = fetched.find((entry) => entry.options.method === "PUT");
  check(Boolean(put) && put.url.endsWith("/config"), "options.js PUTs /config to save");
  check(put && JSON.parse(put.options.body).packet_root === "~/new-packets", "options.js sends the new packet_root");
  check(elements.status.textContent === "saved", "options.js reports saved");
}

// -- background.js: SAVE_PACKET / BURST_PICK capture fallbacks -----------------

const VISIBLE_TAB_PNG = "data:image/png;base64,VISIBLETAB";
const CROP_RECT = { x: 8, y: 120, width: 640, height: 360, dpr: 2 };
const VIDEO_META = {
  title: "Background test",
  sourceUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  // Differs from sourceUrl so a page_url/source_url mix-up is caught.
  pageUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42",
  site: "youtube",
  timestampSec: 42.5,
};
const CANVAS_STILL = { ...VIDEO_META, ok: true, captureMethod: "canvas", imageDataUrl: "data:image/png;base64,CANVAS" };
const PACKET = { id: "01JBACKGROUND000000000000", title: "Background test", source_url: VIDEO_META.sourceUrl, created_at: "2026-10-08T12:00:00+00:00" };
// The helper's place_in_photoshop result, which SAVE_PACKET passes through.
const PHOTOSHOP_PLACED = { ok: true, placed: true };
const PACKET_OK = [201, { ok: true, packet: PACKET, photoshop: PHOTOSHOP_PLACED }];
// What historyEntryFromPacket builds from PACKET (no clipboard, so text is title + url).
const PACKET_HISTORY_ENTRY = {
  id: "01JBACKGROUND000000000000",
  title: "Background test",
  url: VIDEO_META.sourceUrl,
  text: `Background test\n${VIDEO_META.sourceUrl}`,
  createdAt: "2026-10-08T12:00:00+00:00",
};
// Both burst routes also report frame_count, which BURST_PICK does not pass on.
const BURSTS_OK = [201, { ok: true, session_id: "s-canvas", picker_url: "http://127.0.0.1:8787/picker/s-canvas", frame_count: 2 }];
const FFMPEG_OK = [201, { ok: true, session_id: "s-ffmpeg", picker_url: "http://127.0.0.1:8787/picker/s-ffmpeg", frame_count: 15, capture_method: "burst_ffmpeg" }];
// What BURST_PICK returns after each of those.
const BURSTS_PICKED = { ok: true, session_id: "s-canvas", picker_url: "http://127.0.0.1:8787/picker/s-canvas" };
const FFMPEG_PICKED = { ok: true, session_id: "s-ffmpeg", picker_url: "http://127.0.0.1:8787/picker/s-ffmpeg", capture_method: "burst_ffmpeg" };

// Every success case sends both options SAVE_PACKET and BURST_PICK accept.
const SEND_OPTIONS = { name: " My still ", placePhotoshop: true };

// Every helper POST (/packets, /bursts, /bursts/ffmpeg) carries VIDEO_META in
// snake_case plus SEND_OPTIONS as `name` (trimmed) and `photoshop`.
const SHARED_BODY = {
  title: VIDEO_META.title,
  source_url: VIDEO_META.sourceUrl,
  page_url: VIDEO_META.pageUrl,
  site: VIDEO_META.site,
  timestamp_sec: VIDEO_META.timestampSec,
  name: "My still",
  photoshop: true,
};

const sameJson = (actual, expected) => JSON.stringify(actual) === JSON.stringify(expected);
const fieldNames = (object) => Object.keys(object).sort().join();

// `actual` has exactly the fields of `expected`, each with the expected value.
function checkFields(label, actual, expected) {
  check(fieldNames(actual) === fieldNames(expected), `${label} has no missing or extra fields`);
  for (const [field, value] of Object.entries(expected)) {
    check(sameJson(actual[field], value), `${label} ${field}`);
  }
}

// A helper POST body is SHARED_BODY plus the route's own `fields`.
function checkHelperBody(label, body = {}, fields) {
  checkFields(label, body, { ...SHARED_BODY, ...fields });
}

// Every SAVE_PACKET success returns the helper's packet and photoshop result,
// stores the history entry, and stays out of the burst machinery.
function checkSavePacketSuccess(label, bg, response) {
  checkFields(`${label} response`, response, { ok: true, packet: PACKET, photoshop: PHOTOSHOP_PLACED });
  check(sameJson(bg.storage.clipstashHistory, [PACKET_HISTORY_ENTRY]), `${label} stores the full history entry`);
  check(bg.openedTabs.length === 0, `${label} opens no tab`);
  check(bg.pollStarts() === 0, `${label} starts no pending-history polling`);
}

// Every BURST_PICK success returns `expected`, opens only its picker tab, and
// starts pending-history polling once. History is written later, when the
// picker reports the chosen frame.
function checkBurstPickSuccess(label, bg, response, expected) {
  checkFields(`${label} response`, response, expected);
  check(bg.openedTabs.join() === expected.picker_url, `${label} opens only the picker tab`);
  check(bg.pollStarts() === 1, `${label} starts pending-history polling once`);
  check(bg.storage.clipstashHistory === undefined, `${label} writes no history before the pick`);
}

// Loads background.js with chrome/fetch stubs. `scriptResult` is what the
// injected content script returns; `responses` maps helper path -> [status, json].
function loadBackground({ tabUrl = VIDEO_META.pageUrl, scriptResult, responses = {} }) {
  const fetched = [];
  const openedTabs = [];
  const storage = {};
  let visibleTabCaptures = 0;
  let pollStarts = 0;
  let listener = null;
  const sandbox = {
    console,
    URL,
    setInterval: () => {
      pollStarts += 1;
      return 1;
    },
    clearInterval: () => {},
    importScripts(path) {
      runInNewContext(readFileSync(join(root, "extension", path), "utf8"), sandbox, { filename: path });
    },
    fetch: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      fetched.push({ url, method: options.method || "GET", body });
      const [status, json] = responses[new URL(url).pathname] || [404, { ok: false, error: "not stubbed" }];
      return { ok: status < 400, status, json: async () => json };
    },
    chrome: {
      runtime: {
        lastError: undefined,
        onMessage: { addListener: (fn) => (listener = fn) },
      },
      tabs: {
        query: async () => [{ id: 7, windowId: 3, url: tabUrl }],
        captureVisibleTab(_windowId, _opts, callback) {
          visibleTabCaptures += 1;
          callback(VISIBLE_TAB_PNG);
        },
        create(opts, callback) {
          openedTabs.push(opts.url);
          callback({ id: 8 });
        },
      },
      scripting: { executeScript: async () => [{ result: scriptResult }] },
      storage: {
        local: {
          get: async (key) => ({ [key]: storage[key] }),
          set: async (items) => Object.assign(storage, items),
        },
      },
    },
  };
  createContext(sandbox);
  runInNewContext(backgroundSrc, sandbox, { filename: "background.js" });
  return {
    send: (message) => new Promise((resolve) => listener(message, {}, resolve)),
    fetched,
    openedTabs,
    storage,
    visibleTabCaptures: () => visibleTabCaptures,
    pollStarts: () => pollStarts,
    paths: () => fetched.map((entry) => new URL(entry.url).pathname),
  };
}

{
  const bg = loadBackground({ scriptResult: CANVAS_STILL, responses: { "/packets": PACKET_OK } });
  const response = await bg.send({ type: "SAVE_PACKET", ...SEND_OPTIONS });
  check(bg.fetched[0]?.method === "POST" && bg.paths().join() === "/packets", "background SAVE_PACKET canvas POSTs /packets only");
  // image_base64 is the data URL with its "data:image/png;base64," prefix stripped.
  checkHelperBody("background SAVE_PACKET canvas /packets body", bg.fetched[0]?.body, { capture_method: "canvas", image_base64: "CANVAS" });
  checkSavePacketSuccess("background SAVE_PACKET canvas", bg, response);
}

{
  // Warm the captured-urls cache, prove it serves the repeat, then a save must force a refetch.
  const bg = loadBackground({ scriptResult: CANVAS_STILL, responses: { "/packets": PACKET_OK } });
  for (const type of ["GET_CAPTURED_URLS", "GET_CAPTURED_URLS", "SAVE_PACKET", "GET_CAPTURED_URLS"]) await bg.send({ type });
  check(bg.fetched.map((entry) => entry.method).join() === "GET,POST,GET", "background SAVE_PACKET invalidates the captured-urls cache");
}

{
  const bg = loadBackground({
    scriptResult: { ...VIDEO_META, ok: false, tainted: true, captureMethod: "visible_tab", cropRect: CROP_RECT },
    responses: { "/packets": PACKET_OK },
  });
  const response = await bg.send({ type: "SAVE_PACKET", ...SEND_OPTIONS });
  check(bg.paths().join() === "/packets" && bg.visibleTabCaptures() === 1, "background SAVE_PACKET tainted uses captureVisibleTab and POSTs /packets only");
  checkHelperBody("background SAVE_PACKET tainted /packets body", bg.fetched[0]?.body, {
    capture_method: "visible_tab",
    image_base64: "VISIBLETAB",
    crop_rect: CROP_RECT,
  });
  checkSavePacketSuccess("background SAVE_PACKET tainted", bg, response);
}

const BURST_FRAMES = ["data:image/png;base64,ONE", "data:image/png;base64,TWO"];
const CANVAS_BURST = { ...VIDEO_META, ok: true, captureMethod: "burst_canvas", frames: BURST_FRAMES };

{
  const bg = loadBackground({ scriptResult: CANVAS_BURST, responses: { "/bursts": BURSTS_OK } });
  const response = await bg.send({ type: "BURST_PICK", ...SEND_OPTIONS });
  check(bg.paths().join() === "/bursts", "background BURST_PICK canvas POSTs /bursts only");
  checkHelperBody("background BURST_PICK canvas /bursts body", bg.fetched[0]?.body, { capture_method: "burst_canvas", frames: BURST_FRAMES });
  checkBurstPickSuccess("background BURST_PICK canvas", bg, response, BURSTS_PICKED);
}

const TAINTED_BURST = { ...VIDEO_META, ok: false, tainted: true, captureMethod: "burst_visible_tab", cropRect: CROP_RECT };
const MEDIA_URL = "https://r1---sn-abc.googlevideo.com/videoplayback?expire=123";
// The /bursts/ffmpeg body's own field; the helper does the cropping and labelling.
const FFMPEG_FIELDS = { media_url: MEDIA_URL };
// The single cropped visible-tab frame /bursts gets when ffmpeg is unavailable.
const VISIBLE_TAB_BURST_FIELDS = { capture_method: "burst_visible_tab", frames: [VISIBLE_TAB_PNG], crop_rect: CROP_RECT };

{
  const bg = loadBackground({
    scriptResult: { ...TAINTED_BURST, mediaUrl: MEDIA_URL },
    responses: { "/bursts/ffmpeg": FFMPEG_OK },
  });
  const response = await bg.send({ type: "BURST_PICK", ...SEND_OPTIONS });
  check(bg.paths().join() === "/bursts/ffmpeg", "background BURST_PICK tainted tries /bursts/ffmpeg only");
  check(bg.visibleTabCaptures() === 0, "background BURST_PICK ffmpeg OK skips captureVisibleTab");
  checkHelperBody("background BURST_PICK ffmpeg OK /bursts/ffmpeg body", bg.fetched[0]?.body, FFMPEG_FIELDS);
  checkBurstPickSuccess("background BURST_PICK ffmpeg OK", bg, response, FFMPEG_PICKED);
}

{
  const bg = loadBackground({
    scriptResult: { ...TAINTED_BURST, mediaUrl: MEDIA_URL },
    responses: { "/bursts/ffmpeg": [503, { ok: false, error: "ffmpeg not found" }], "/bursts": BURSTS_OK },
  });
  const response = await bg.send({ type: "BURST_PICK", ...SEND_OPTIONS });
  check(bg.paths().join() === "/bursts/ffmpeg,/bursts", "background BURST_PICK ffmpeg 503 falls back to POST /bursts");
  check(bg.visibleTabCaptures() === 1, "background BURST_PICK ffmpeg 503 uses captureVisibleTab");
  checkHelperBody("background BURST_PICK ffmpeg 503 /bursts/ffmpeg body", bg.fetched[0]?.body, FFMPEG_FIELDS);
  checkHelperBody("background BURST_PICK ffmpeg 503 /bursts body", bg.fetched[1]?.body, VISIBLE_TAB_BURST_FIELDS);
  checkBurstPickSuccess("background BURST_PICK ffmpeg 503", bg, response, BURSTS_PICKED);
}

{
  const bg = loadBackground({ scriptResult: TAINTED_BURST, responses: { "/bursts": BURSTS_OK } });
  const response = await bg.send({ type: "BURST_PICK", ...SEND_OPTIONS });
  check(bg.paths().join() === "/bursts", "background BURST_PICK without mediaUrl never calls /bursts/ffmpeg");
  check(bg.visibleTabCaptures() === 1, "background BURST_PICK without mediaUrl uses captureVisibleTab");
  checkHelperBody("background BURST_PICK without mediaUrl /bursts body", bg.fetched[0]?.body, VISIBLE_TAB_BURST_FIELDS);
  checkBurstPickSuccess("background BURST_PICK without mediaUrl", bg, response, BURSTS_PICKED);
}

// Without options, both entry points send photoshop false and no name (a blank
// name counts as none). SAVE_PACKET returns photoshop null when the helper
// placed nothing.
for (const [type, scriptResult] of [["SAVE_PACKET", CANVAS_STILL], ["BURST_PICK", CANVAS_BURST]]) {
  const bg = loadBackground({ scriptResult, responses: { "/packets": [201, { ok: true, packet: PACKET }], "/bursts": BURSTS_OK } });
  const response = await bg.send({ type, name: "   " });
  const body = bg.fetched[0]?.body || {};
  const label = `background ${type} without options`;
  check(response.ok === true && body.photoshop === false, `${label} sends photoshop false`);
  check(!("name" in body), `${label} sends no name`);
  if (type === "SAVE_PACKET") check(response.photoshop === null, `${label} returns photoshop null`);
}

// Both entry points guard the active tab. The script results and helper stubs
// would succeed, so a missing guard shows up as a helper call, tab or poll.
for (const [type, scriptResult] of [["SAVE_PACKET", CANVAS_STILL], ["BURST_PICK", CANVAS_BURST]]) {
  const bg = loadBackground({
    tabUrl: "chrome://extensions/",
    scriptResult,
    responses: { "/packets": PACKET_OK, "/bursts": BURSTS_OK },
  });
  const response = await bg.send({ type });
  const label = `background ${type} on a non-http(s) tab`;
  check(response.ok === false && response.error === "active tab is not a http(s) page", `${label} is rejected`);
  check(bg.fetched.length === 0, `${label} never calls the helper`);
  check(bg.openedTabs.length === 0, `${label} opens no tab`);
  check(bg.pollStarts() === 0, `${label} starts no pending-history polling`);
}

if (failures > 0) {
  console.error(`\n${failures} smoke check(s) failed`);
  process.exit(1);
}
console.log("\nall extension smoke checks passed");
