"""Native multi-frame burst extraction via ffmpeg.

When an in-page canvas burst is tainted by cross-origin media (e.g.
googlevideo on YouTube), the extension sends the video's media URL to
``POST /bursts/ffmpeg`` instead of falling straight back to a single
visible-tab still. This module fetches that media to a temp file and asks
ffmpeg to extract one PNG per seek position around the capture timestamp,
mirroring the extension's canvas burst: ``±7 × 0.15s`` around the centre
(up to 15 frames, deduped when negative offsets collapse at ``t=0``).

ffmpeg is an optional system dependency discovered via ``shutil.which`` and
is never installed as a pip dependency. yt-dlp is an optional subprocess
dependency used only for YouTube / googlevideo media URLs; when it is
missing those URLs fall back to a plain HTTP GET (which may or may not be a
fetchable direct media URL). Every network / subprocess call has a timeout;
nothing here hangs forever.

Public API
----------
``ffmpeg_available()``
    True when an ``ffmpeg`` binary is on PATH.
``ytdlp_available()``
    True when a ``yt-dlp`` binary is on PATH.
``extract_burst_pngs(media_path, timestamp_sec, *, step, n)``
    Run ffmpeg once per seek position and return PNG bytes.
``fetch_media_to_temp(media_url, *, site, page_url)``
    Download / copy media to a fresh temp file; caller must clean up.
``burst_frames_from_url(media_url, timestamp_sec, *, site, page_url)``
    fetch → extract → cleanup, returning the PNG frame list.

``fetch_media_to_temp`` also accepts ``file://`` URLs (copies the local file
into the temp dir). This keeps the tests hermetic: no network required.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

BURST_STEP = 0.15
BURST_N = 7  # offsets -7..+7 → match extension (±7 × 0.15s)
DOWNLOAD_TIMEOUT_SEC = 60  # hard cap; never hang forever
FFMPEG_TIMEOUT_SEC = 30
YTDLP_TIMEOUT_SEC = 90

USER_AGENT = "clipstash"
TEMP_PREFIX = "clipstash-ffmpeg-"


class FFmpegBurstError(Exception):
    """Raised when a native ffmpeg burst cannot be produced."""


def ffmpeg_available() -> bool:
    """True when an ``ffmpeg`` binary is on PATH."""
    return shutil.which("ffmpeg") is not None


def ytdlp_available() -> bool:
    """True when a ``yt-dlp`` binary is on PATH."""
    return shutil.which("yt-dlp") is not None


def _ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegBurstError("ffmpeg not found on PATH (brew install ffmpeg)")
    return path


def _coerce_timestamp(timestamp_sec: float | None) -> float:
    if timestamp_sec in (None, ""):
        return 0.0
    try:
        return max(0.0, float(timestamp_sec))
    except (TypeError, ValueError) as exc:
        raise FFmpegBurstError(f"invalid timestamp_sec: {timestamp_sec!r}") from exc


def _seek_targets(timestamp_sec: float, step: float, n: int) -> list[float]:
    """Seek positions ``±n*step`` around the timestamp, clamped at zero.

    Negative offsets near ``t=0`` collapse onto ``0.0``; those duplicates are
    dropped (mirrors the extension's ``seekTargets`` clamping behaviour).
    """
    targets: list[float] = []
    seen: set[float] = set()
    for offset in (i * step for i in range(-n, n + 1)):
        target = round(max(0.0, timestamp_sec + offset), 6)
        if target not in seen:
            seen.add(target)
            targets.append(target)
    return targets


def extract_burst_pngs(
    media_path: Path,
    timestamp_sec: float,
    *,
    step: float = BURST_STEP,
    n: int = BURST_N,
) -> list[bytes]:
    """Seek ``±n*step`` around *timestamp_sec*; return PNG bytes per frame.

    Runs one ffmpeg invocation per seek position (``-ss`` before ``-i``, one
    video frame, PNG output) inside a private temp dir that is always
    removed. Raises :class:`FFmpegBurstError` when ffmpeg is missing, times
    out, or produces no frame.
    """
    if not ffmpeg_available():
        raise FFmpegBurstError("ffmpeg not found on PATH (brew install ffmpeg)")
    media = Path(media_path)
    if not media.is_file():
        raise FFmpegBurstError(f"media file not found: {media}")
    center = _coerce_timestamp(timestamp_sec)
    targets = _seek_targets(center, step, n)
    if not targets:
        raise FFmpegBurstError("burst requires at least one seek target")

    ffmpeg = _ffmpeg_path()
    workdir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    try:
        frames: list[bytes] = []
        for index, target in enumerate(targets):
            out_path = workdir / f"frame_{index:03d}.png"
            command = [
                ffmpeg,
                "-y",
                "-ss",
                f"{target:.3f}",
                "-i",
                str(media),
                "-frames:v",
                "1",
                "-q:v",
                "1",
                str(out_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=FFMPEG_TIMEOUT_SEC,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise FFmpegBurstError(
                    f"ffmpeg timed out after {FFMPEG_TIMEOUT_SEC}s at {target:.3f}s"
                ) from exc
            if completed.returncode != 0:
                stderr = completed.stderr.decode("utf-8", "replace").strip()
                raise FFmpegBurstError(
                    f"ffmpeg failed to extract frame at {target:.3f}s: {stderr[-500:]}"
                )
            if not out_path.exists() or out_path.stat().st_size == 0:
                raise FFmpegBurstError(f"ffmpeg produced no frame at {target:.3f}s")
            frames.append(out_path.read_bytes())
        return frames
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _prefer_ytdlp(parsed, site: str) -> bool:
    """True when the URL should go through yt-dlp (YouTube / googlevideo)."""
    if str(site or "").strip().lower() == "youtube":
        return True
    host = (parsed.hostname or "").lower()
    return "youtube" in host or "googlevideo" in host


def _download_with_ytdlp(media_url: str, tmpdir: Path) -> Path:
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        raise FFmpegBurstError("yt-dlp not found on PATH (brew install yt-dlp)")
    template = str(tmpdir / "media.%(ext)s")
    command = [
        ytdlp,
        "--no-playlist",
        "-f",
        "mp4/best",
        "-o",
        template,
        "--",
        media_url,
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=YTDLP_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegBurstError(f"yt-dlp timed out after {YTDLP_TIMEOUT_SEC}s") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        raise FFmpegBurstError(f"yt-dlp failed: {stderr[-500:]}")
    files = [
        path
        for path in tmpdir.iterdir()
        if path.is_file()
        and path.name.startswith("media.")
        and not path.name.endswith(".part")
    ]
    if not files:
        raise FFmpegBurstError("yt-dlp downloaded no media file")
    return files[0]


def _download_with_urllib(media_url: str, tmpdir: Path) -> Path:
    target = tmpdir / "media.bin"
    request = urllib.request.Request(media_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SEC) as response:
            with target.open("wb") as fh:
                shutil.copyfileobj(response, fh)
    except Exception as exc:
        raise FFmpegBurstError(f"download failed: {exc}") from exc
    if not target.exists() or target.stat().st_size == 0:
        raise FFmpegBurstError("download produced an empty file")
    return target


def fetch_media_to_temp(
    media_url: str,
    *,
    site: str = "generic",
    page_url: str | None = None,
) -> Path:
    """Download *media_url* to a fresh temp file; caller must clean up.

    Prefers yt-dlp when *site* is ``youtube`` or the host looks like
    YouTube / googlevideo and yt-dlp is on PATH (optional subprocess
    dependency, not a pip dep). Otherwise downloads with a plain HTTP GET
    (User-Agent: clipstash). ``file://`` URLs are copied locally so tests can
    stay hermetic. Raises :class:`FFmpegBurstError` on any failure; every
    network/subprocess call is bounded by a timeout.
    """
    media_url = str(media_url or "").strip()
    if not media_url:
        raise FFmpegBurstError("media_url is required")
    parsed = urlparse(media_url)
    if parsed.scheme == "file":
        source = Path(urllib.request.url2pathname(parsed.path))
        if not source.is_file():
            raise FFmpegBurstError(f"local media file not found: {source}")
        tmpdir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
        try:
            target = tmpdir / f"media{source.suffix or '.mp4'}"
            shutil.copyfile(source, target)
            return target
        except Exception:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise
    if parsed.scheme not in ("http", "https"):
        raise FFmpegBurstError(
            f"unsupported media_url scheme: {parsed.scheme!r} (use http(s) or file://)"
        )

    tmpdir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    try:
        if _prefer_ytdlp(parsed, site) and ytdlp_available():
            return _download_with_ytdlp(media_url, tmpdir)
        return _download_with_urllib(media_url, tmpdir)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def burst_frames_from_url(
    media_url: str,
    timestamp_sec: float,
    *,
    site: str = "generic",
    page_url: str | None = None,
) -> list[bytes]:
    """fetch → extract → cleanup temp download → return PNG list (len >= 1)."""
    media_path = fetch_media_to_temp(media_url, site=site, page_url=page_url)
    tmpdir = media_path.parent
    try:
        frames = extract_burst_pngs(media_path, timestamp_sec)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if not frames:
        raise FFmpegBurstError("burst extraction produced no frames")
    return frames
