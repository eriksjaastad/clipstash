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

if (failures > 0) {
  console.error(`\n${failures} smoke check(s) failed`);
  process.exit(1);
}
console.log("\nall extension smoke checks passed");
