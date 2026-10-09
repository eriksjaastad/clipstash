"""Tests for helper.ffmpeg_burst (native multi-frame burst via ffmpeg).

Requires an ``ffmpeg`` binary on PATH; every test in this module is skipped
cleanly when it is missing. No network access: media fetches use ``file://``
URLs pointing at the committed ``tests/fixtures/sample_burst.mp4``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from support import request, server_url

import helper.ffmpeg_burst as ffmpeg_burst
from helper.ffmpeg_burst import (
    FFmpegBurstError,
    burst_frames_from_url,
    extract_burst_pngs,
    fetch_media_to_temp,
    ffmpeg_available,
)
from helper.packets import read_record

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_burst.mp4"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

pytestmark = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg not on PATH (brew install ffmpeg)"
)


# --------------------------------------------------------------------------
# frame extraction
# --------------------------------------------------------------------------

def test_extract_burst_pngs_dedupes_collapsed_offsets_near_zero() -> None:
    # At t=0.1 the seven negative offsets all clamp to 0.0 and collapse into a
    # single frame; the remaining positive offsets stay unique → 9 frames.
    frames = extract_burst_pngs(FIXTURE, 0.1)
    assert len(frames) == 9
    assert all(frame.startswith(PNG_MAGIC) for frame in frames)


def test_extract_burst_pngs_custom_step_and_n() -> None:
    frames = extract_burst_pngs(FIXTURE, 1.0, step=0.3, n=2)
    assert len(frames) == 5
    assert all(frame.startswith(PNG_MAGIC) for frame in frames)


def test_extract_burst_pngs_missing_media_raises() -> None:
    with pytest.raises(FFmpegBurstError, match="media file not found"):
        extract_burst_pngs(Path("/no/such/media.mp4"), 1.0)


# --------------------------------------------------------------------------
# fetch + burst_frames_from_url (file:// keeps tests hermetic)
# --------------------------------------------------------------------------

def test_fetch_media_to_temp_file_url_copies_locally() -> None:
    path = fetch_media_to_temp(FIXTURE.as_uri())
    try:
        assert path.is_file()
        assert path.read_bytes() == FIXTURE.read_bytes()
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_burst_frames_from_url_file_url_returns_pngs_and_cleans_temp(
    monkeypatch,
) -> None:
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(prefix=None):
        path = Path(real_mkdtemp(prefix=prefix))
        created.append(path)
        return path

    monkeypatch.setattr(ffmpeg_burst.tempfile, "mkdtemp", tracking_mkdtemp)
    frames = burst_frames_from_url(FIXTURE.as_uri(), 1.0, site="generic")
    assert len(frames) == 15
    assert all(frame.startswith(PNG_MAGIC) for frame in frames)
    assert created, "expected fetch and extract to use temp dirs"
    assert all(not path.exists() for path in created), "temp dirs must be cleaned up"


# --------------------------------------------------------------------------
# missing ffmpeg / bad URLs must fail clearly and quickly
# --------------------------------------------------------------------------

def test_missing_ffmpeg_raises_clear_error(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg_burst.shutil, "which", lambda _name: None)
    assert ffmpeg_available() is False
    with pytest.raises(FFmpegBurstError) as exc:
        extract_burst_pngs(FIXTURE, 1.0)
    assert (type(exc.value), str(exc.value)) == (
        FFmpegBurstError, "ffmpeg not found on PATH (brew install ffmpeg)"
    )
    with pytest.raises(FFmpegBurstError, match="ffmpeg not found"):
        burst_frames_from_url(FIXTURE.as_uri(), 1.0)
    # Direct callers download first, so a bad URL fails before the ffmpeg check.
    with pytest.raises(FFmpegBurstError, match="download failed"):
        burst_frames_from_url("http://127.0.0.1:1/nope.mp4", 1.0)


def test_unsupported_scheme_raises_clear_error() -> None:
    with pytest.raises(FFmpegBurstError, match="unsupported media_url scheme"):
        fetch_media_to_temp("ftp://example.com/media.mp4")


# --------------------------------------------------------------------------
# POST /bursts/ffmpeg integration
# --------------------------------------------------------------------------

def test_ffmpeg_burst_endpoint_creates_session_picker_and_choose(server, tmp_path) -> None:
    payload = {
        "media_url": FIXTURE.as_uri(),
        "timestamp_sec": 1.0,
        "title": "Native burst",
        "source_url": "https://www.youtube.com/watch?v=burst123",
        "page_url": "https://www.youtube.com/watch?v=burst123",
        "site": "youtube",
    }
    status, _, body = request(
        server_url(server, "/bursts/ffmpeg"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    data = json.loads(body)
    assert data["ok"] is True
    assert data["capture_method"] == "burst_ffmpeg"
    assert data["frame_count"] > 1
    session_id = data["session_id"]
    assert data["picker_url"] == f"http://127.0.0.1:{server.server_address[1]}/picker/{session_id}"

    # Choosing a frame writes a packet labeled burst_ffmpeg.
    status, _, body = request(
        server_url(server, f"/picker/{session_id}/choose"),
        data=json.dumps(
            {
                "frame_index": 0,
                "title": "Native burst",
                "source_url": "https://www.youtube.com/watch?v=burst123",
                "site": "youtube",
                "capture_method": "burst_ffmpeg",
            }
        ).encode("utf-8"),
        method="POST",
    )
    assert status == 200
    chosen = json.loads(body)
    assert chosen["ok"] is True
    packet = chosen["packet"]
    assert packet["capture_method"] == "burst_ffmpeg"
    assert read_record(packet["id"], root=tmp_path)["capture_method"] == "burst_ffmpeg"
    assert (
        tmp_path / "youtube" / packet["id"] / "native-burst.png"
    ).read_bytes().startswith(PNG_MAGIC)


def test_ffmpeg_burst_endpoint_requires_media_url(server) -> None:
    status, _, body = request(
        server_url(server, "/bursts/ffmpeg"),
        data=json.dumps({"timestamp_sec": 1.0}).encode("utf-8"),
        method="POST",
    )
    assert status == 400
    assert "media_url" in json.loads(body)["error"]


def test_ffmpeg_burst_endpoint_503_when_ffmpeg_missing(server, monkeypatch) -> None:
    # Checked before any download: this unreachable URL would otherwise be a 502.
    monkeypatch.setattr(ffmpeg_burst.shutil, "which", lambda _name: None)
    status, _, body = request(
        server_url(server, "/bursts/ffmpeg"),
        data=json.dumps({"media_url": "http://127.0.0.1:1/nope.mp4"}).encode("utf-8"),
        method="POST",
    )
    assert (status, body) == (
        503, b'{"ok": false, "error": "ffmpeg not found on PATH (brew install ffmpeg)"}'
    )


def test_ffmpeg_burst_endpoint_bad_url_returns_502(server, monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg_burst, "DOWNLOAD_TIMEOUT_SEC", 2)
    status, _, body = request(
        server_url(server, "/bursts/ffmpeg"),
        data=json.dumps(
            {
                "media_url": "http://127.0.0.1:1/nope.mp4",
                "timestamp_sec": 1.0,
            }
        ).encode("utf-8"),
        method="POST",
    )
    assert status == 502
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "download failed" in payload["error"]

def test_ytdlp_prefers_page_url_for_youtube(monkeypatch) -> None:
    """YouTube CDN media URLs must not be handed to yt-dlp; use page_url."""
    seen: list[str] = []

    def fake_ytdlp(url: str, tmpdir: Path) -> Path:
        seen.append(url)
        target = tmpdir / "media.mp4"
        target.write_bytes(FIXTURE.read_bytes())
        return target

    monkeypatch.setattr(ffmpeg_burst, "ytdlp_available", lambda: True)
    monkeypatch.setattr(ffmpeg_burst, "_download_with_ytdlp", fake_ytdlp)
    path = fetch_media_to_temp(
        "https://googlevideo.com/videoplayback?expire=1",
        site="youtube",
        page_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    try:
        assert seen == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
        assert path.is_file()
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_ytdlp_fetch_url_takes_youtube_pages_by_host_else_media_url() -> None:
    media = "https://cdn.example.com/clip.mp4"
    assert ffmpeg_burst._ytdlp_fetch_url(media, None, "generic") == media
    assert ffmpeg_burst._ytdlp_fetch_url(media, "https://youtu.be/x", "generic") == "https://youtu.be/x"


# Every subprocess call times out, or exits 1 with 604 chars of stderr ending "TAIL".
TIMED_OUT = subprocess.TimeoutExpired("tool", 1)
EXITED_1 = subprocess.CompletedProcess("tool", 1, stdout=None, stderr=b"x" * 600 + b"TAIL\n")
STDERR_TAIL = "x" * 496 + "TAIL"


def fake_subprocess(monkeypatch, outcome) -> None:
    def run(command, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ffmpeg_burst.subprocess, "run", run)


def test_ffmpeg_timeout_and_nonzero_exit_messages(monkeypatch) -> None:
    fake_subprocess(monkeypatch, TIMED_OUT)
    with pytest.raises(FFmpegBurstError) as timed_out:
        extract_burst_pngs(FIXTURE, 2.0)
    fake_subprocess(monkeypatch, EXITED_1)
    with pytest.raises(FFmpegBurstError) as failed:
        extract_burst_pngs(FIXTURE, 2.0)
    assert str(timed_out.value) == "ffmpeg timed out after 30s at 0.950s"
    assert str(failed.value) == f"ffmpeg failed to extract frame at 0.950s: {STDERR_TAIL}"


def test_ytdlp_timeout_and_nonzero_exit_messages(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ffmpeg_burst.shutil, "which", lambda name: f"/fake/{name}")
    fake_subprocess(monkeypatch, TIMED_OUT)
    with pytest.raises(FFmpegBurstError) as timed_out:
        ffmpeg_burst._download_with_ytdlp("https://youtu.be/x", tmp_path)
    fake_subprocess(monkeypatch, EXITED_1)
    with pytest.raises(FFmpegBurstError) as failed:
        ffmpeg_burst._download_with_ytdlp("https://youtu.be/x", tmp_path)
    assert str(timed_out.value) == "yt-dlp timed out after 90s"
    assert str(failed.value) == f"yt-dlp failed: {STDERR_TAIL}"

