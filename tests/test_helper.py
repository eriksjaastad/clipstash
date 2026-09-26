"""Tests for the clipstash helper (packet schema, storage, HTTP endpoints)."""

from __future__ import annotations

import base64
import json
import threading
import urllib.request

import pytest

from helper import __version__
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
