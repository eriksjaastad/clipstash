"""Tests for the clipstash helper (packet schema, storage, HTTP endpoints, crop)."""

from __future__ import annotations

import base64
import io
import json
import threading
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from helper import __version__
from helper import bursts
from helper.bursts import create_burst, load_session, session_dir
from helper.config import (
    ConfigError,
    default_packet_root,
    effective_packet_root,
    load_config,
    save_config,
)
from helper.crop import apply_crop_rect
from helper.packets import (
    default_root,
    export_csv,
    find_packet_dir,
    list_packets,
    new_record,
    normalize_site,
    packet_dir,
    packet_image_path,
    read_record,
    ulid,
    write_packet,
)
from helper.server import create_server

PNG_1PX_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
PNG_1PX = base64.b64decode(PNG_1PX_B64)

GREEN = (0, 255, 0)
RED = (255, 0, 0)


def make_png_with_green_region(width=200, height=100, region=(10, 20, 50, 50)) -> bytes:
    """Solid red PNG with a green rectangle at (left, top, right, bottom)."""
    image = Image.new("RGB", (width, height), RED)
    for x in range(region[0], region[2]):
        for y in range(region[1], region[3]):
            image.putpixel((x, y), GREEN)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def png_size_and_pixel(image_bytes: bytes, xy=(0, 0)):
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.load()
        return image.size, image.getpixel(xy)


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
    assert record["image"] == "how-i-edit.png"
    assert record["name"] == "how-i-edit"
    assert record["capture_method"] == "canvas"
    assert record["clipboard"] == {
        "title": "How I edit",
        "url": "https://example.com/v=1",
        "text": "How I edit\nhttps://example.com/v=1",
    }


def test_new_record_name_override_is_slugified():
    record = new_record("How I edit", "https://example.com/v=1", name="My Still! FINAL")
    assert record["image"] == "my-still-final.png"
    assert record["name"] == "my-still-final"


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
    assert written["image"] == "demo.png"
    assert written["name"] == "demo"

    directory = tmp_path / "youtube" / record["id"]
    assert (directory / "demo.png").read_bytes() == PNG_1PX
    assert (directory / "record.yaml").exists()

    reread = read_record(record["id"], root=tmp_path)
    assert reread["title"] == "Demo"
    assert reread["source_url"] == "https://example.com/v=1"
    assert reread["image"] == "demo.png"


def test_write_packet_layout_uses_site_folder(tmp_path):
    youtube = new_record("YouTube clip", "https://example.com/1", site="youtube")
    generic = new_record("Generic clip", "https://example.com/2", site="generic")
    write_packet(youtube, PNG_1PX, root=tmp_path)
    write_packet(generic, PNG_1PX, root=tmp_path)

    assert (tmp_path / "youtube" / youtube["id"] / "youtube-clip.png").exists()
    assert (tmp_path / "generic" / generic["id"] / "generic-clip.png").exists()
    assert packet_dir(tmp_path, youtube["id"], site="youtube") == tmp_path / "youtube" / youtube["id"]


def test_write_packet_unknown_site_falls_back_to_generic(tmp_path):
    record = new_record("Weird site", "https://example.com/1", site="../../etc")
    written = write_packet(record, PNG_1PX, root=tmp_path)
    assert written["site"] == "generic"
    assert (tmp_path / "generic" / record["id"] / "weird-site.png").exists()


def test_write_packet_name_override_is_always_reslugified(tmp_path):
    record = new_record("Title", "https://example.com/1", site="youtube", name="already-clean")
    written = write_packet(record, PNG_1PX, root=tmp_path, name="  My Raw! Name  ")
    assert written["name"] == "my-raw-name"
    assert written["image"] == "my-raw-name.png"
    assert (tmp_path / "youtube" / record["id"] / "my-raw-name.png").exists()


def test_read_record_legacy_flat_layout(tmp_path):
    directory = tmp_path / "01JLEGACY0000000000000000"
    directory.mkdir(parents=True)
    (directory / "still.png").write_bytes(PNG_1PX)
    (directory / "record.yaml").write_text(
        "id: 01JLEGACY0000000000000000\n"
        "title: Legacy flat\n"
        "source_url: https://example.com/legacy\n"
        "image: still.png\n",
        encoding="utf-8",
    )

    record = read_record("01JLEGACY0000000000000000", root=tmp_path)
    assert record["title"] == "Legacy flat"
    assert record["image"] == "still.png"
    assert find_packet_dir("01JLEGACY0000000000000000", root=tmp_path) == directory
    assert packet_image_path(tmp_path, record) == directory / "still.png"


def test_read_record_missing_packet_fails_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="no packet with id"):
        read_record("01JMISSING000000000000000", root=tmp_path)


def test_normalize_site_allows_only_adapter_ids():
    assert normalize_site("YouTube") == "youtube"
    assert normalize_site("x") == "x"
    assert normalize_site("../../evil") == "generic"
    assert normalize_site("") == "generic"


def test_list_and_export_csv(tmp_path):
    first = new_record("First", "https://example.com/1", packet_id="01JFIRST0000000000000000", site="youtube")
    second = new_record("Second", "https://example.com/2", packet_id="01JSECOND000000000000000", site="tiktok")
    write_packet(first, PNG_1PX, root=tmp_path)
    write_packet(second, PNG_1PX, root=tmp_path)

    summaries = list_packets(root=tmp_path)
    assert [s["title"] for s in summaries] == ["Second", "First"]
    assert summaries[0]["source_url"] == "https://example.com/2"
    assert {s["site"] for s in summaries} == {"youtube", "tiktok"}

    csv_text = export_csv(root=tmp_path)
    assert "id,created_at,title,source_url" in csv_text
    assert "name" in csv_text.splitlines()[0]
    assert "First" in csv_text and "Second" in csv_text


def test_list_packets_sees_site_and_legacy_flat(tmp_path):
    record = new_record("Site packet", "https://example.com/site", site="youtube")
    write_packet(record, PNG_1PX, root=tmp_path)

    legacy_dir = tmp_path / "01JLEGACY0000000000000000"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "still.png").write_bytes(PNG_1PX)
    (legacy_dir / "record.yaml").write_text(
        "id: 01JLEGACY0000000000000000\n"
        "title: Legacy packet\n"
        "source_url: https://example.com/legacy\n"
        "image: still.png\n",
        encoding="utf-8",
    )

    summaries = list_packets(root=tmp_path)
    titles = {s["title"] for s in summaries}
    assert titles == {"Site packet", "Legacy packet"}


# --------------------------------------------------------------------------
# crop_rect (tainted visible_tab stills)
# --------------------------------------------------------------------------

def test_apply_crop_rect_unit_css_pixels():
    png = make_png_with_green_region()
    cropped = apply_crop_rect(
        png, {"x": 10, "y": 20, "width": 40, "height": 30, "dpr": 1}
    )
    assert png_size_and_pixel(cropped, (0, 0)) == ((40, 30), GREEN)
    assert png_size_and_pixel(cropped, (39, 29))[1] == GREEN


def test_apply_crop_rect_device_pixel_ratio():
    png = make_png_with_green_region(region=(10, 10, 50, 30))
    cropped = apply_crop_rect(
        png, {"x": 5, "y": 5, "width": 20, "height": 10, "dpr": 2}
    )
    # device box: left=10, top=10, right=50, bottom=30
    assert png_size_and_pixel(cropped, (0, 0)) == ((40, 20), GREEN)
    assert png_size_and_pixel(cropped, (39, 19))[1] == GREEN


def test_apply_crop_rect_invalid_rects_return_original_bytes():
    png = make_png_with_green_region()
    invalid_rects = [
        None,
        "garbage",
        [],
        {},
        {"x": None, "y": 0, "width": 10, "height": 10},
        {"x": "a", "y": 0, "width": 10, "height": 10},
        {"x": float("nan"), "y": 0, "width": 10, "height": 10},
        {"x": -1000, "y": -1000, "width": 10, "height": 10},
        {"x": 5000, "y": 5000, "width": 10, "height": 10},
        {"x": 0, "y": 0, "width": 0, "height": 10},
        {"x": 0, "y": 0, "width": -5, "height": 10},
    ]
    for crop_rect in invalid_rects:
        assert apply_crop_rect(png, crop_rect) == png, f"rect {crop_rect!r}"


def test_apply_crop_rect_bad_dpr_falls_back_to_1():
    png = make_png_with_green_region()
    for dpr in (None, 0, -2, float("inf"), float("nan"), "abc"):
        cropped = apply_crop_rect(
            png, {"x": 0, "y": 0, "width": 10, "height": 10, "dpr": dpr}
        )
        assert png_size_and_pixel(cropped, (0, 0)) == ((10, 10), RED)


# --------------------------------------------------------------------------
# HTTP endpoints
# --------------------------------------------------------------------------

@pytest.fixture()
def server(tmp_path):
    httpd = create_server(
        host="127.0.0.1", port=0, root=str(tmp_path), config_path=tmp_path / "config.json"
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    thread.join(timeout=5)
    httpd.server_close()


def request(url: str, data: bytes | None = None, method: str | None = None, headers: dict | None = None):
    headers = dict(headers or {})
    if method in ("POST", "PUT") and data is not None and "Content-Type" not in headers:
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
    assert record["image"] == "test-packet.png"
    assert record["name"] == "test-packet"
    assert record["capture_method"] == "canvas"

    status, _, body = request(server_url(server, "/packets"))
    payload = json.loads(body)
    assert status == 200
    assert len(payload["packets"]) == 1
    assert payload["packets"][0]["id"] == record["id"]


def test_post_packet_name_override_writes_slugged_still(server, tmp_path):
    payload = make_payload(name="  My Still! FINAL  ")
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    record = json.loads(body)["packet"]
    assert record["image"] == "my-still-final.png"
    assert record["name"] == "my-still-final"
    assert (tmp_path / "youtube" / record["id"] / "my-still-final.png").exists()


def test_post_packet_empty_name_uses_title_slug(server, tmp_path):
    payload = make_payload(name="   ")
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    record = json.loads(body)["packet"]
    assert record["image"] == "test-packet.png"


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


def test_post_packet_visible_tab_crop_rect_crops_still(server, tmp_path):
    png = make_png_with_green_region()
    payload = make_payload(
        capture_method="visible_tab",
        image_base64=base64.b64encode(png).decode("ascii"),
        crop_rect={"x": 10, "y": 20, "width": 40, "height": 30, "dpr": 1},
    )
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    record = json.loads(body)["packet"]
    still = (tmp_path / record["site"] / record["id"] / record["image"]).read_bytes()
    assert png_size_and_pixel(still, (0, 0)) == ((40, 30), GREEN)
    # crop_rect is an ephemeral request field, never persisted.
    assert "crop_rect" not in read_record(record["id"], root=tmp_path)


def test_post_packet_visible_tab_without_crop_rect_keeps_full_still(server, tmp_path):
    png = make_png_with_green_region()
    payload = make_payload(
        capture_method="visible_tab",
        image_base64=base64.b64encode(png).decode("ascii"),
    )
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    record = json.loads(body)["packet"]
    still = (tmp_path / record["site"] / record["id"] / record["image"]).read_bytes()
    assert png_size_and_pixel(still, (0, 0)) == ((200, 100), RED)


def test_post_packet_multipart(server, tmp_path):
    boundary = "clipstash-test-boundary"
    crop_rect = {"x": 10, "y": 20, "width": 40, "height": 30, "dpr": 1}
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
        'Content-Disposition: form-data; name="crop_rect"',
        "",
        json.dumps(crop_rect),
        f"--{boundary}",
        'Content-Disposition: form-data; name="image"; filename="still.png"',
        "Content-Type: image/png",
        "",
    ]
    body = ("\r\n".join(parts) + "\r\n").encode("utf-8")
    body += make_png_with_green_region() + f"\r\n--{boundary}--\r\n".encode("utf-8")
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
    assert payload["packet"]["image"] == "multipart-packet.png"
    still = (
        tmp_path / payload["packet"]["site"] / payload["packet"]["id"] / payload["packet"]["image"]
    ).read_bytes()
    assert png_size_and_pixel(still, (0, 0)) == ((40, 30), GREEN)


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
# config endpoint + root resolution
# --------------------------------------------------------------------------

def test_config_endpoint_get_shape(server, tmp_path):
    status, content_type, body = request(server_url(server, "/config"))
    payload = json.loads(body)
    assert status == 200
    assert content_type == "application/json"
    assert payload["ok"] is True
    assert payload["packet_root"] == str(Path(tmp_path).resolve())
    assert payload["default_root"] == str(default_packet_root())
    assert payload["config_path"] == str(tmp_path / "config.json")


def test_put_config_persists_updates_live_root_and_writes(server, tmp_path):
    new_root = tmp_path / "new-packets"
    status, _, body = request(
        server_url(server, "/config"),
        data=json.dumps({"packet_root": str(new_root)}).encode("utf-8"),
        method="PUT",
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["packet_root"] == str(new_root.resolve())

    # GET returns the new root.
    status, _, body = request(server_url(server, "/config"))
    assert status == 200
    assert json.loads(body)["packet_root"] == str(new_root.resolve())

    # New saves land under the new root without a restart.
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(make_payload(title="Under new root")).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    record = json.loads(body)["packet"]
    assert (new_root / record["site"] / record["id"] / record["image"]).exists()
    assert (new_root / record["site"] / record["id"] / "record.yaml").exists()

    # The root was persisted on disk.
    config_path = server.RequestHandlerClass.config_path
    assert load_config(config_path) == {"packet_root": str(new_root.resolve())}

    # Simulate a helper restart: no CLI root, same config file.
    restarted = create_server(host="127.0.0.1", port=0, root=None, config_path=config_path)
    assert restarted.RequestHandlerClass.root == str(new_root.resolve())
    restarted.server_close()


def test_put_config_rejects_invalid_root(server):
    for bad in ("", "   ", 42, None, "relative/path"):
        status, _, body = request(
            server_url(server, "/config"),
            data=json.dumps({"packet_root": bad}).encode("utf-8"),
            method="PUT",
        )
        assert status == 400, f"packet_root={bad!r} should be rejected"
        assert json.loads(body)["ok"] is False


def test_effective_packet_root_priority(monkeypatch, tmp_path):
    cli_root = tmp_path / "cli"
    config_root = tmp_path / "config"
    env_root = tmp_path / "env"
    config_path = tmp_path / "config.json"

    save_config({"packet_root": str(config_root)}, config_path)
    monkeypatch.setenv("CLIPSTASH_ROOT", str(env_root))

    # CLI wins.
    assert effective_packet_root(cli_root, config_path=config_path) == cli_root
    # Config beats env.
    assert effective_packet_root(None, config_path=config_path) == config_root.resolve()
    # Env beats the built-in default when no config exists.
    assert effective_packet_root(None, config_path=tmp_path / "missing.json") == env_root
    # Default with no overrides at all.
    monkeypatch.delenv("CLIPSTASH_ROOT")
    assert effective_packet_root(None, config_path=tmp_path / "missing.json") == default_root()


# --------------------------------------------------------------------------
# unusable config.json: visible error, never a silent fallback root
# --------------------------------------------------------------------------

def test_missing_config_file_still_gives_default_root(monkeypatch, tmp_path):
    monkeypatch.delenv("CLIPSTASH_ROOT", raising=False)
    missing = tmp_path / "missing.json"
    assert load_config(missing) == {}
    assert effective_packet_root(None, config_path=missing) == default_root()


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "\"just a string\""])
def test_corrupt_config_raises_naming_the_file(tmp_path, content):
    config_path = tmp_path / "config.json"
    config_path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=str(config_path)):
        load_config(config_path)
    with pytest.raises(ConfigError, match=str(config_path)):
        effective_packet_root(None, config_path=config_path)


@pytest.mark.parametrize("bad_root", ["relative/path", "", "   ", 42, None])
def test_invalid_configured_root_raises_instead_of_falling_back(monkeypatch, tmp_path, bad_root):
    monkeypatch.setenv("CLIPSTASH_ROOT", str(tmp_path / "env"))
    config_path = tmp_path / "config.json"
    save_config({"packet_root": bad_root}, config_path)
    with pytest.raises(ConfigError, match="invalid packet_root in config file"):
        effective_packet_root(None, config_path=config_path)
    # An explicit CLI root still wins without reading the config.
    assert effective_packet_root(tmp_path / "cli", config_path=config_path) == tmp_path / "cli"


def test_create_server_refuses_corrupt_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match=str(config_path)):
        create_server(host="127.0.0.1", port=0, root=None, config_path=config_path)


def test_cli_exits_nonzero_with_message_on_corrupt_config(monkeypatch, tmp_path, capsys):
    from helper.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "Clipstash" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text("{not json", encoding="utf-8")
    assert main(["export"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "clipstashd: cannot read config file" in captured.err
    assert str(config_path) in captured.err


def test_cli_exits_nonzero_on_relative_configured_root(monkeypatch, tmp_path, capsys):
    from helper.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "Clipstash" / "config.json"
    save_config({"packet_root": "relative/path"}, config_path)
    assert main(["serve"]) == 1
    assert "invalid packet_root in config file" in capsys.readouterr().err


def test_put_config_reports_corrupt_config_file(server):
    config_path = Path(server.RequestHandlerClass.config_path)
    config_path.write_text("{not json", encoding="utf-8")
    new_root = config_path.parent / "new-root"
    status, _, body = request(
        server_url(server, "/config"),
        data=json.dumps({"packet_root": str(new_root)}).encode("utf-8"),
        method="PUT",
    )
    payload = json.loads(body)
    assert status == 500
    assert payload["ok"] is False
    assert str(config_path) in payload["error"]
    # The unreadable file is left for the user to inspect, not overwritten.
    assert config_path.read_text(encoding="utf-8") == "{not json"


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
    assert packet["image"] == "chosen-burst-frame.png"

    # Packet is on disk under the server's packet root (site folder).
    record = read_record(packet["id"], root=tmp_path)
    assert record["title"] == "Chosen burst frame"
    assert (
        tmp_path / "youtube" / packet["id"] / "chosen-burst-frame.png"
    ).read_bytes() == PNG_1PX

    # Session is gone after choose.
    status, _, _ = request(server_url(server, f"/picker/{session_id}"))
    assert status == 404
    status, _, _ = request(server_url(server, f"/picker/{session_id}/frame/1"))
    assert status == 404


def test_burst_name_override_flows_to_chosen_packet(server, tmp_path):
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(make_burst_payload(frame_count=1, name="Burst Still!")).encode(
            "utf-8"
        ),
        method="POST",
    )
    assert status == 201
    session_id = json.loads(body)["session_id"]

    status, _, body = request(
        server_url(server, f"/picker/{session_id}/choose"),
        data=json.dumps({"frame_index": 0}).encode("utf-8"),
        method="POST",
    )
    assert status == 200
    packet = json.loads(body)["packet"]
    assert packet["image"] == "burst-still.png"
    assert (tmp_path / "youtube" / packet["id"] / "burst-still.png").exists()


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


def test_burst_visible_tab_crop_rect_crops_frame_and_chosen_packet(server, tmp_path):
    png = make_png_with_green_region()
    payload = make_burst_payload(
        frame_count=1,
        capture_method="burst_visible_tab",
        frames=[f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"],
        crop_rect={"x": 10, "y": 20, "width": 40, "height": 30, "dpr": 1},
    )
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    session_id = json.loads(body)["session_id"]

    # The single fallback frame served to the picker is already cropped.
    status, _, body = request(server_url(server, f"/picker/{session_id}/frame/0"))
    assert status == 200
    assert png_size_and_pixel(body, (0, 0)) == ((40, 30), GREEN)

    # Choosing it writes the cropped still as the packet.
    status, _, body = request(
        server_url(server, f"/picker/{session_id}/choose"),
        data=json.dumps(
            {
                "frame_index": 0,
                "title": "Cropped burst fallback",
                "source_url": "https://www.youtube.com/watch?v=burst123",
                "capture_method": "burst_visible_tab",
            }
        ).encode("utf-8"),
        method="POST",
    )
    assert status == 200
    packet = json.loads(body)["packet"]
    still = (
        tmp_path / packet["site"] / packet["id"] / packet["image"]
    ).read_bytes()
    assert png_size_and_pixel(still, (0, 0)) == ((40, 30), GREEN)
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


def test_burst_choose_enqueues_pending_history_and_drains(server):
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(make_burst_payload(frame_count=1)).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    session_id = json.loads(body)["session_id"]

    status, _, body = request(
        server_url(server, f"/picker/{session_id}/choose"),
        data=json.dumps(
            {
                "frame_index": 0,
                "title": "Pending history burst",
                "source_url": "https://www.youtube.com/watch?v=burst123",
                "site": "youtube",
                "capture_method": "burst_canvas",
            }
        ).encode("utf-8"),
        method="POST",
    )
    assert status == 200
    packet = json.loads(body)["packet"]

    status, _, body = request(server_url(server, "/history/pending"))
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["entries"] == [
        {
            "id": packet["id"],
            "title": "Pending history burst",
            "url": "https://www.youtube.com/watch?v=burst123",
            "text": "Pending history burst\nhttps://www.youtube.com/watch?v=burst123",
            "createdAt": packet["created_at"],
        }
    ]

    # The endpoint drains the queue: a second GET is empty.
    status, _, body = request(server_url(server, "/history/pending"))
    assert status == 200
    assert json.loads(body) == {"ok": True, "entries": []}


def test_burst_picker_html_dispatches_chosen_event(server):
    status, _, body = request(
        server_url(server, "/bursts"),
        data=json.dumps(make_burst_payload(frame_count=1)).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    session_id = json.loads(body)["session_id"]

    status, _, body = request(server_url(server, f"/picker/{session_id}"))
    assert status == 200
    html = body.decode("utf-8")
    assert 'new CustomEvent("clipstash:chosen"' in html
    assert "detail: payload.packet" in html


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
