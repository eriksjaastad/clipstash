"""Tests for the optional Photoshop place mode (macOS-only, mocked)."""

from __future__ import annotations

import hashlib
import json
import subprocess

from support import PNG_1PX, PNG_1PX_B64, post_multipart_packet, request, server_url

from helper import bursts, photoshop
from helper.photoshop import env_photoshop_enabled, place_in_photoshop


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


def place_mocked(monkeypatch, tmp_path, run):
    """place_in_photoshop on a real PNG, with macOS forced and subprocess.run faked."""
    write_sample_png(tmp_path)
    monkeypatch.setattr(photoshop, "is_macos", lambda: True)
    monkeypatch.setattr(photoshop.subprocess, "run", run)
    return place_in_photoshop(tmp_path / "still.png")


# --------------------------------------------------------------------------
# script text
# --------------------------------------------------------------------------


def test_placement_scripts_are_byte_identical_to_main():
    # sha256 of each script on main @ 879a890 (git show 879a890:helper/photoshop.py),
    # in fallback order. A change here changes what osascript runs.
    digests = [
        (name, hashlib.sha256(script.encode("utf-8")).hexdigest())
        for name, script in photoshop._PLACEMENT_SCRIPTS
    ]
    assert digests == [
        ("place", "d66d7c373857440ac9c8bf87399553ef6d547efa3443712c14e03a9c2d23781d"),
        ("duplicate", "e3b9d1dc89f129f7f659c8ec84327575df49f7e5f3b22c41fed9c0bf595bc245"),
        ("open", "3afa30a05c051842168e947d42394a52e898ad273432b463b588cd9207d1c12c"),
    ]


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
    run, calls = fake_run(stdout="CLIPSTASH_PLACED\n")
    result = place_mocked(monkeypatch, tmp_path, run)
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
    run, calls = fake_run(stdout="CLIPSTASH_NOT_RUNNING\n")
    result = place_mocked(monkeypatch, tmp_path, run)
    assert (result["ok"], result["reason"], len(calls)) == (False, "photoshop_not_running", 1)


def test_no_document_is_definitive(monkeypatch, tmp_path):
    run, calls = fake_run(
        stdout="CLIPSTASH_NO_DOCUMENT: Adobe Photoshop got an error: no document is open\n"
    )
    result = place_mocked(monkeypatch, tmp_path, run)
    assert (result["ok"], result["reason"], len(calls)) == (False, "photoshop_no_document", 1)


def test_no_document_timeout_classified_as_timeout(monkeypatch, tmp_path):
    run, calls = fake_run(
        stdout="CLIPSTASH_NO_DOCUMENT: Adobe Photoshop 2026 got an error: AppleEvent timed out. (-1712)\n"
    )
    result = place_mocked(monkeypatch, tmp_path, run)
    assert (result["ok"], result["reason"], len(calls)) == (False, "photoshop_timeout", 1)


def test_compile_failure_falls_back_to_duplicate_script(monkeypatch, tmp_path):
    calls = []
    outputs = [
        (1, "script error: Expected end of line, etc. but found identifier. (-2741)\n"),
        (0, "CLIPSTASH_PLACED\n"),
    ]

    def _run(command, **kwargs):
        calls.append(command)
        returncode, output = outputs[min(len(calls), len(outputs)) - 1]
        return subprocess.CompletedProcess(command, returncode, stdout=output if returncode == 0 else "", stderr=output if returncode != 0 else "")

    result = place_mocked(monkeypatch, tmp_path, _run)
    assert (result["ok"], result["method"], len(calls)) == (True, "duplicate", 2)


def test_all_scripts_compile_failure(monkeypatch, tmp_path):
    run, _calls = fake_run(
        returncode=1, stderr="script error: Expected end of line but found identifier. (-2741)\n"
    )
    result = place_mocked(monkeypatch, tmp_path, run)
    assert (result["ok"], result["reason"], len(result["details"])) == (False, "photoshop_unsupported", 3)


def test_osascript_missing(monkeypatch, tmp_path):
    result = place_mocked(monkeypatch, tmp_path, fake_run(raise_exc=FileNotFoundError("osascript"))[0])
    assert (result["ok"], result["reason"]) == (False, "osascript_missing")


def test_osascript_timeout_expired(monkeypatch, tmp_path):
    run, _calls = fake_run(raise_exc=subprocess.TimeoutExpired("osascript", 120))
    result = place_mocked(monkeypatch, tmp_path, run)
    assert (result["ok"], result["reason"]) == (False, "photoshop_timeout")


def test_automation_denied_stderr(monkeypatch, tmp_path):
    run, calls = fake_run(returncode=1, stderr="osascript is not allowed to send events. (-1743)\n")
    result = place_mocked(monkeypatch, tmp_path, run)
    assert (result["ok"], result["reason"], len(calls)) == (False, "photoshop_automation_denied", 1)


def test_photoshop_not_installed_stderr(monkeypatch, tmp_path):
    run, calls = fake_run(
        returncode=1,
        stderr="script error: Can’t get application id \"com.adobe.Photoshop\". (-1728)\n",
    )
    result = place_mocked(monkeypatch, tmp_path, run)
    assert (result["ok"], result["reason"], len(calls)) == (False, "photoshop_not_installed", 1)


def test_error_marker_is_osascript_failed_with_the_full_failure_shape(monkeypatch, tmp_path):
    result = place_mocked(monkeypatch, tmp_path, fake_run(stdout="CLIPSTASH_ERROR: boom\n")[0])
    assert list(result.items()) == [
        ("ok", False),
        ("error", "Photoshop place failed: boom"),
        ("reason", "photoshop_osascript_failed"),
        ("image", str(tmp_path / "still.png")),
    ]


def test_exit_zero_without_marker_is_unexpected_output(monkeypatch, tmp_path):
    result = place_mocked(monkeypatch, tmp_path, fake_run(stdout="")[0])
    assert (result["reason"], result["error"]) == (
        "photoshop_unexpected_output",
        "osascript returned no result marker (empty output)",
    )


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


def test_post_multipart_packet_photoshop_field_places(server, tmp_path, monkeypatch):
    calls = patch_place(monkeypatch)
    status, _, body = post_multipart_packet(
        server, title="Photoshop packet", source_url="https://example.com/1", photoshop="true"
    )
    assert status == 201
    assert calls == [tmp_path / "generic" / json.loads(body)["packet"]["id"] / "photoshop-packet.png"]


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
