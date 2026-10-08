"""Tests for the optional Photoshop place mode (macOS-only, mocked)."""

from __future__ import annotations

import json
import subprocess

from support import PNG_1PX, PNG_1PX_B64, request, server_url

from helper import bursts, photoshop
from helper.photoshop import (
    build_duplicate_script,
    build_open_script,
    build_place_script,
    env_photoshop_enabled,
    place_in_photoshop,
    scripts_for_placement,
)


def make_payload(**overrides):
    payload = {
        "title": "Photoshop packet",
        "source_url": "https://www.youtube.com/watch?v=ps123",
        "page_url": "https://www.youtube.com/watch?v=ps123",
        "site": "youtube",
        "timestamp_sec": 12.5,
        "capture_method": "canvas",
        "image_base64": PNG_1PX_B64,
    }
    payload.update(overrides)
    return payload


def make_burst_payload(**overrides):
    payload = {
        "title": "Photoshop burst",
        "source_url": "https://www.youtube.com/watch?v=psburst",
        "page_url": "https://www.youtube.com/watch?v=psburst",
        "site": "youtube",
        "timestamp_sec": 42.5,
        "capture_method": "burst_canvas",
        "frames": [f"data:image/png;base64,{PNG_1PX_B64}"],
    }
    payload.update(overrides)
    return payload


def write_sample_png(tmp_path, name="still.png") -> object:
    path = tmp_path / name
    path.write_bytes(PNG_1PX)
    return path


def fake_run(stdout="", stderr="", returncode=0, raise_exc=None):
    calls = []

    def _run(command, **kwargs):
        calls.append((command, kwargs))
        if raise_exc:
            raise raise_exc
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

    return _run, calls


# --------------------------------------------------------------------------
# script generation / dry-run
# --------------------------------------------------------------------------


def test_scripts_receive_path_as_argv_not_interpolation():
    for script in scripts_for_placement():
        text = script["script"]
        assert "on run argv" in text
        assert "com.adobe.Photoshop" in text
        assert "POSIX file imagePath" in text
        assert "/tmp/secret path.png" not in text


def test_place_script_uses_legacy_place_verb():
    assert "place docRef file imageFile" in build_place_script()
    assert "CLIPSTASH_PLACED" in build_place_script()


def test_duplicate_script_opens_then_duplicates_layers():
    script = build_duplicate_script()
    assert "open imageFile showing dialogs never" in script
    assert "duplicate every art layer of openedDoc to targetDoc" in script
    assert "close openedDoc saving no" in script


def test_open_script_is_last_resort():
    script = build_open_script()
    assert "open imageFile showing dialogs never" in script
    assert "CLIPSTASH_OPENED" in script
    assert "duplicate" not in script


def test_dry_run_does_not_require_image_or_osascript(tmp_path):
    result = place_in_photoshop(tmp_path / "does-not-exist.png", dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert len(result["scripts"]) == 3
    assert [entry["name"] for entry in result["scripts"]] == ["place", "duplicate", "open"]
    assert all(cmd[0] == "osascript" for cmd in result["commands"])


def test_non_darwin_returns_clear_error(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: False)
    result = place_in_photoshop(tmp_path / "still.png")
    assert result["ok"] is False
    assert result["reason"] == "unsupported_platform"
    assert "macOS-only" in result["error"]


def test_missing_image_returns_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    result = place_in_photoshop(tmp_path / "missing.png")
    assert result["ok"] is False
    assert result["reason"] == "missing_image"


def test_env_flag_parsing(monkeypatch):
    for value in ("1", "TRUE", "yes", "on"):
        monkeypatch.setenv("CLIPSTASH_PHOTOSHOP", value)
        assert env_photoshop_enabled() is True, value
    monkeypatch.setenv("CLIPSTASH_PHOTOSHOP", "0")
    assert env_photoshop_enabled() is False
    monkeypatch.delenv("CLIPSTASH_PHOTOSHOP", raising=False)
    assert env_photoshop_enabled() is False


# --------------------------------------------------------------------------
# osascript classification via mocked subprocess
# --------------------------------------------------------------------------


def test_place_success_mocked(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    run, calls = fake_run(stdout="CLIPSTASH_PLACED\n")
    monkeypatch.setattr(photoshop.subprocess, "run", run)

    result = place_in_photoshop(tmp_path / "still.png")

    assert result == {
        "ok": True,
        "placed": True,
        "image": str(tmp_path / "still.png"),
        "method": "place",
    }
    assert len(calls) == 1
    command = calls[0][0]
    assert command[0] == "osascript"
    assert command[-1] == str(tmp_path / "still.png")


def test_not_running_is_definitive_and_does_not_fall_back(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    run, calls = fake_run(stdout="CLIPSTASH_NOT_RUNNING\n")
    monkeypatch.setattr(photoshop.subprocess, "run", run)

    result = place_in_photoshop(tmp_path / "still.png")

    assert result["ok"] is False
    assert result["reason"] == "photoshop_not_running"
    assert len(calls) == 1


def test_no_document_is_definitive(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    run, calls = fake_run(
        stdout="CLIPSTASH_NO_DOCUMENT: Adobe Photoshop got an error: no document is open\n"
    )
    monkeypatch.setattr(photoshop.subprocess, "run", run)

    result = place_in_photoshop(tmp_path / "still.png")

    assert result["ok"] is False
    assert result["reason"] == "photoshop_no_document"
    assert len(calls) == 1


def test_no_document_timeout_classified_as_timeout(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    run, calls = fake_run(
        stdout="CLIPSTASH_NO_DOCUMENT: Adobe Photoshop 2026 got an error: AppleEvent timed out. (-1712)\n"
    )
    monkeypatch.setattr(photoshop.subprocess, "run", run)

    result = place_in_photoshop(tmp_path / "still.png")

    assert result["ok"] is False
    assert result["reason"] == "photoshop_timeout"
    assert len(calls) == 1


def test_compile_failure_falls_back_to_duplicate_script(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)

    calls = []
    outputs = [
        (1, "script error: Expected end of line, etc. but found identifier. (-2741)\n"),
        (0, "CLIPSTASH_PLACED\n"),
    ]

    def _run(command, **kwargs):
        calls.append(command)
        returncode, output = outputs[min(len(calls), len(outputs)) - 1]
        return subprocess.CompletedProcess(command, returncode, stdout=output if returncode == 0 else "", stderr=output if returncode != 0 else "")

    monkeypatch.setattr(photoshop.subprocess, "run", _run)

    result = place_in_photoshop(tmp_path / "still.png")

    assert result["ok"] is True
    assert result["method"] == "duplicate"
    assert len(calls) == 2


def test_all_scripts_compile_failure(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)

    def _run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="script error: Expected end of line but found identifier. (-2741)\n",
        )

    monkeypatch.setattr(photoshop.subprocess, "run", _run)

    result = place_in_photoshop(tmp_path / "still.png")

    assert result["ok"] is False
    assert result["reason"] == "photoshop_unsupported"
    assert len(result["details"]) == 3


def test_osascript_missing(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    run, _calls = fake_run(raise_exc=FileNotFoundError("osascript"))
    monkeypatch.setattr(photoshop.subprocess, "run", run)

    result = place_in_photoshop(tmp_path / "still.png")
    assert result["ok"] is False
    assert result["reason"] == "osascript_missing"


def test_osascript_timeout_expired(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)

    def _run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 120)

    monkeypatch.setattr(photoshop.subprocess, "run", _run)

    result = place_in_photoshop(tmp_path / "still.png")
    assert result["ok"] is False
    assert result["reason"] == "photoshop_timeout"


def test_automation_denied_stderr(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    run, calls = fake_run(
        returncode=1,
        stderr="osascript is not allowed to send events. (-1743)\n",
    )
    monkeypatch.setattr(photoshop.subprocess, "run", run)

    result = place_in_photoshop(tmp_path / "still.png")

    assert result["ok"] is False
    assert result["reason"] == "photoshop_automation_denied"
    assert len(calls) == 1


def test_photoshop_not_installed_stderr(monkeypatch, tmp_path):
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    run, calls = fake_run(
        returncode=1,
        stderr="script error: Can’t get application id \"com.adobe.Photoshop\". (-1728)\n",
    )
    monkeypatch.setattr(photoshop.subprocess, "run", run)

    result = place_in_photoshop(tmp_path / "still.png")

    assert result["ok"] is False
    assert result["reason"] == "photoshop_not_installed"
    assert len(calls) == 1


# --------------------------------------------------------------------------
# HTTP endpoints (placement always mocked)
# --------------------------------------------------------------------------

def patch_place(monkeypatch):
    calls = []

    def _fake(image_path, **kwargs):
        calls.append(image_path)
        return {"ok": True, "placed": True, "image": str(image_path), "method": "mock"}

    monkeypatch.setattr("helper.server.place_in_photoshop", _fake)
    return calls


def test_post_packet_photoshop_flag_places(server, tmp_path, monkeypatch):
    calls = patch_place(monkeypatch)
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(make_payload(photoshop=True)).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["photoshop"] == {
        "ok": True,
        "placed": True,
        "image": str(
            tmp_path / "youtube" / payload["packet"]["id"] / "photoshop-packet.png"
        ),
        "method": "mock",
    }
    assert calls == [tmp_path / "youtube" / payload["packet"]["id"] / "photoshop-packet.png"]


def test_post_packet_without_flag_does_not_place(server, monkeypatch):
    calls = patch_place(monkeypatch)
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(make_payload()).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    assert "photoshop" not in json.loads(body)
    assert calls == []


def test_place_photoshop_endpoint(server, tmp_path, monkeypatch):
    _, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(make_payload()).encode("utf-8"),
        method="POST",
    )
    packet_id = json.loads(body)["packet"]["id"]

    calls = patch_place(monkeypatch)
    status, _, body = request(
        server_url(server, f"/packets/{packet_id}/place-photoshop"),
        data=b"",
        method="POST",
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["packet_id"] == packet_id
    assert payload["photoshop"]["ok"] is True
    assert calls == [tmp_path / "youtube" / packet_id / "photoshop-packet.png"]


def test_place_photoshop_endpoint_missing_packet_404(server, monkeypatch):
    calls = patch_place(monkeypatch)
    status, _, body = request(
        server_url(server, "/packets/01JNOPE000000000000000000/place-photoshop"),
        data=b"",
        method="POST",
    )
    assert status == 404
    assert json.loads(body)["ok"] is False
    assert calls == []


def test_burst_session_stores_flag_and_choose_places(server, tmp_path, monkeypatch):
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(make_burst_payload(photoshop=True)).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    session_id = json.loads(body)["session_id"]
    assert bursts.load_session(session_id)["photoshop"] is True

    calls = patch_place(monkeypatch)
    status, _, body = request(
        server_url(server, f"/picker/{session_id}/choose"),
        data=json.dumps({"frame_index": 0}).encode("utf-8"),
        method="POST",
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    packet_id = payload["packet"]["id"]
    assert payload["photoshop"] == {
        "ok": True,
        "placed": True,
        "image": str(tmp_path / "youtube" / packet_id / "photoshop-burst.png"),
        "method": "mock",
    }
    assert calls == [tmp_path / "youtube" / packet_id / "photoshop-burst.png"]


def test_burst_choose_without_flag_does_not_place(server, monkeypatch):
    _, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(make_burst_payload()).encode("utf-8"),
        method="POST",
    )
    session_id = json.loads(body)["session_id"]

    calls = patch_place(monkeypatch)
    status, _, body = request(
        server_url(server, f"/picker/{session_id}/choose"),
        data=json.dumps({"frame_index": 0}).encode("utf-8"),
        method="POST",
    )
    assert status == 200
    assert "photoshop" not in json.loads(body)
    assert calls == []


def test_server_auto_photoshop_places_without_request_flag(start_server, tmp_path, monkeypatch):
    httpd = start_server(photoshop_auto=True)
    calls = patch_place(monkeypatch)
    status, _, body = request(
        server_url(httpd, "/packets"),
        data=json.dumps(make_payload()).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    payload = json.loads(body)
    assert payload["photoshop"]["ok"] is True
    assert calls == [
        tmp_path / "youtube" / payload["packet"]["id"] / "photoshop-packet.png"
    ]
