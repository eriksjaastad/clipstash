"""Tests for the clipstash helper (packet schema, storage, HTTP endpoints)."""

from __future__ import annotations

import base64
import json
import threading
import urllib.request

import pytest

from helper import __version__
from helper import bursts
from helper.bursts import create_burst, load_session, session_dir
from helper.packets import (
    export_csv,
    list_packets,
    new_record,
    read_record,
    ulid,
    write_packet,
)
from helper.server import create_server

PNG_1PX_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
PNG_1PX = base64.b64decode(PNG_1PX_B64)


def make_payload(**overrides):
    payload = {
        "title": "Test packet",
        "source_url": "https://www.youtube.com/watch?v=abc123",
        "page_url": "https://www.youtube.com/watch?v=abc123",
        "site": "youtube",
        "timestamp_sec": 12.5,
        "capture_method": "canvas",
        "image_base64": PNG_1PX_B64,
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# packet schema / storage
# --------------------------------------------------------------------------

def test_ulid_shape():
    value = ulid()
    assert len(value) == 26
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in value)


def test_new_record_has_clipboard_pack():
    record = new_record("How I edit", "https://example.com/v=1")
    assert record["id"]
    assert record["created_at"].endswith("+00:00")
    assert record["image"] == "still.png"
    assert record["capture_method"] == "canvas"
    assert record["clipboard"] == {
        "title": "How I edit",
        "url": "https://example.com/v=1",
        "text": "How I edit\nhttps://example.com/v=1",
    }


def test_new_record_capture_method():
    record = new_record(
        "Visible tab", "https://example.com/v=1", capture_method="visible_tab"
    )
    assert record["capture_method"] == "visible_tab"


def test_new_record_requires_fields():
    with pytest.raises(ValueError):
        new_record("", "https://example.com")
    with pytest.raises(ValueError):
        new_record("Title", "")


def test_write_and_read_packet(tmp_path):
    record = new_record("Demo", "https://example.com/v=1", site="youtube")
    written = write_packet(record, PNG_1PX, root=tmp_path)
    assert written["image"] == "still.png"

    directory = tmp_path / record["id"]
    assert (directory / "still.png").read_bytes() == PNG_1PX
    assert (directory / "record.yaml").exists()

    reread = read_record(record["id"], root=tmp_path)
    assert reread["title"] == "Demo"
    assert reread["source_url"] == "https://example.com/v=1"


def test_list_and_export_csv(tmp_path):
    first = new_record("First", "https://example.com/1", packet_id="01JFIRST0000000000000000")
    second = new_record("Second", "https://example.com/2", packet_id="01JSECOND000000000000000")
    write_packet(first, PNG_1PX, root=tmp_path)
    write_packet(second, PNG_1PX, root=tmp_path)

    summaries = list_packets(root=tmp_path)
    assert [s["title"] for s in summaries] == ["Second", "First"]
    assert summaries[0]["source_url"] == "https://example.com/2"

    csv_text = export_csv(root=tmp_path)
    assert "id,created_at,title,source_url" in csv_text
    assert "First" in csv_text and "Second" in csv_text


# --------------------------------------------------------------------------
# HTTP endpoints
# --------------------------------------------------------------------------

@pytest.fixture()
def server(tmp_path):
    httpd = create_server(host="127.0.0.1", port=0, root=str(tmp_path))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    thread.join(timeout=5)
    httpd.server_close()


def request(url: str, data: bytes | None = None, method: str | None = None, headers: dict | None = None):
    headers = dict(headers or {})
    if method == "POST" and data is not None and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read()


def server_url(httpd, path: str) -> str:
    host, port = httpd.server_address
    return f"http://{host}:{port}{path}"


def test_health_endpoint(server):
    status, content_type, body = request(server_url(server, "/health"))
    payload = json.loads(body)
    assert status == 200
    assert payload == {"ok": True, "version": __version__}


def test_post_packet_json_and_list(server):
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(make_payload()).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    payload = json.loads(body)
    assert payload["ok"] is True
    record = payload["packet"]
    assert record["title"] == "Test packet"
    assert record["site"] == "youtube"
    assert record["image"] == "still.png"
    assert record["capture_method"] == "canvas"

    status, _, body = request(server_url(server, "/packets"))
    payload = json.loads(body)
    assert status == 200
    assert len(payload["packets"]) == 1
    assert payload["packets"][0]["id"] == record["id"]


def test_post_packet_capture_method_visible_tab(server):
    payload = make_payload(capture_method="visible_tab")
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    record = json.loads(body)["packet"]
    assert record["capture_method"] == "visible_tab"

    status, _, body = request(server_url(server, "/export.csv"))
    header = body.decode("utf-8").splitlines()[0]
    assert "capture_method" in header


def test_post_packet_multipart(server):
    boundary = "clipstash-test-boundary"
    parts = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="title"',
        "",
        "Multipart packet",
        f"--{boundary}",
        'Content-Disposition: form-data; name="source_url"',
        "",
        "https://example.com/multi",
        f"--{boundary}",
        'Content-Disposition: form-data; name="site"',
        "",
        "generic",
        f"--{boundary}",
        'Content-Disposition: form-data; name="image"; filename="still.png"',
        "Content-Type: image/png",
        "",
    ]
    body = ("\r\n".join(parts) + "\r\n").encode("utf-8")
    body += PNG_1PX + f"\r\n--{boundary}--\r\n".encode("utf-8")
    content_type = f"multipart/form-data; boundary={boundary}"

    req = urllib.request.Request(
        server_url(server, "/packets"),
        data=body,
        method="POST",
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 201
        payload = json.loads(resp.read())
    assert payload["packet"]["title"] == "Multipart packet"
    assert payload["packet"]["source_url"] == "https://example.com/multi"


def test_post_packet_requires_image(server):
    payload = make_payload(image_base64="")
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 400
    assert "image_base64" in json.loads(body)["error"]


def test_export_csv_endpoint(server):
    request(
        server_url(server, "/packets"),
        data=json.dumps(make_payload(title="CSV row")).encode("utf-8"),
        method="POST",
    )
    status, content_type, body = request(server_url(server, "/export.csv"))
    text = body.decode("utf-8")
    assert status == 200
    assert content_type == "text/csv"
    assert "CSV row" in text
    assert text.splitlines()[0].startswith("id,created_at")


def test_unknown_route_404(server):
    status, _, _ = request(server_url(server, "/nope"))
    assert status == 404


# --------------------------------------------------------------------------
# burst sessions + picker
# --------------------------------------------------------------------------

def make_burst_payload(frame_count=2, **overrides):
    payload = {
        "title": "Burst packet",
        "source_url": "https://www.youtube.com/watch?v=burst123",
        "page_url": "https://www.youtube.com/watch?v=burst123",
        "site": "youtube",
        "timestamp_sec": 42.5,
        "capture_method": "burst_canvas",
        "frames": [f"data:image/png;base64,{PNG_1PX_B64}" for _ in range(frame_count)],
    }
    payload.update(overrides)
    return payload


def test_create_burst_choose_frame_and_packet(server, tmp_path):
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(make_burst_payload()).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    payload = json.loads(body)
    assert payload["ok"] is True
    session_id = payload["session_id"]
    assert payload["picker_url"] == f"http://127.0.0.1:{server.server_address[1]}/picker/{session_id}"
    assert payload["frame_count"] == 2

    # Picker page renders a grid with both frames.
    status, content_type, body = request(server_url(server, f"/picker/{session_id}"))
    assert status == 200
    assert content_type == "text/html; charset=utf-8"
    html = body.decode("utf-8")
    assert "Burst &amp; pick" in html
    assert f"/picker/{session_id}/frame/0" in html
    assert f"/picker/{session_id}/frame/1" in html

    # Frame bytes are served back as PNG.
    status, content_type, body = request(
        server_url(server, f"/picker/{session_id}/frame/1")
    )
    assert status == 200
    assert content_type == "image/png"
    assert body == PNG_1PX

    # Choosing frame 1 writes a normal packet and deletes the session.
    status, _, body = request(
        server_url(server, f"/picker/{session_id}/choose"),
        data=json.dumps(
            {
                "frame_index": 1,
                "title": "Chosen burst frame",
                "source_url": "https://www.youtube.com/watch?v=burst123",
                "site": "youtube",
                "capture_method": "burst_canvas",
            }
        ).encode("utf-8"),
        method="POST",
    )
    assert status == 200
    chosen = json.loads(body)
    assert chosen["ok"] is True
    packet = chosen["packet"]
    assert packet["title"] == "Chosen burst frame"
    assert packet["capture_method"] == "burst_canvas"
    assert packet["image"] == "still.png"

    # Packet is on disk under the server's packet root.
    record = read_record(packet["id"], root=tmp_path)
    assert record["title"] == "Chosen burst frame"
    assert (tmp_path / packet["id"] / "still.png").read_bytes() == PNG_1PX

    # Session is gone after choose.
    status, _, _ = request(server_url(server, f"/picker/{session_id}"))
    assert status == 404
    status, _, _ = request(server_url(server, f"/picker/{session_id}/frame/1"))
    assert status == 404


def test_burst_visible_tab_capture_method_round_trip(server, tmp_path):
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(
            make_burst_payload(frame_count=1, capture_method="burst_visible_tab")
        ).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    session_id = json.loads(body)["session_id"]

    status, _, body = request(
        server_url(server, f"/picker/{session_id}/choose"),
        data=json.dumps(
            {
                "frame_index": 0,
                "title": "Fallback burst",
                "source_url": "https://www.youtube.com/watch?v=burst123",
                "capture_method": "burst_visible_tab",
            }
        ).encode("utf-8"),
        method="POST",
    )
    assert status == 200
    packet = json.loads(body)["packet"]
    assert packet["capture_method"] == "burst_visible_tab"
    assert read_record(packet["id"], root=tmp_path)["capture_method"] == "burst_visible_tab"


def test_burst_requires_frames(server):
    payload = make_burst_payload(frame_count=0)
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 400
    assert "frames" in json.loads(body)["error"]


def test_burst_rejects_non_png_frames(server):
    payload = make_burst_payload(frame_count=1)
    payload["frames"] = ["data:image/png;base64,bm90YXBuZw=="]
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 400
    assert "PNG" in json.loads(body)["error"]


def test_burst_choose_missing_session_404(server):
    status, _, body = request(
        server_url(server, "/picker/01JNOPE000000000000000000/choose"),
        data=json.dumps({"frame_index": 0}).encode("utf-8"),
        method="POST",
    )
    assert status == 404


def test_burst_session_expiry(monkeypatch):
    meta = create_burst([PNG_1PX], {"title": "t", "source_url": "https://example.com"})
    session_id = meta["session_id"]
    assert load_session(session_id)["frame_count"] == 1

    monkeypatch.setattr(bursts, "SESSION_TTL_SECONDS", -1)
    with pytest.raises(FileNotFoundError):
        load_session(session_id)
    assert not session_dir(session_id).exists()


def test_burst_picker_escapes_metadata(server):
    payload = make_burst_payload(frame_count=1)
    payload["title"] = '<img src=x onerror="alert(1)">'
    payload["source_url"] = 'https://example.com/v"</script><script>alert(2)</script>'
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    session_id = json.loads(body)["session_id"]

    status, _, body = request(server_url(server, f"/picker/{session_id}"))
    assert status == 200
    html = body.decode("utf-8")
    assert '<img src=x' not in html
    assert "&lt;img src=x" in html
    assert "</script><script>alert(2)</script>" not in html
