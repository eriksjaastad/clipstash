"""Tests for the clipstash helper (packet schema, storage, HTTP endpoints, crop)."""

from __future__ import annotations

import base64
import errno
import http.client
import io
import json
import os
import re
import urllib.request
from pathlib import Path

import pytest
import yaml
from PIL import Image
from support import PNG_1PX, PNG_1PX_B64, post_multipart_packet, request, server_url

from helper import __version__
from helper import bursts, ffmpeg_burst
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


def send_json(httpd, path, payload, method="POST"):
    return request(server_url(httpd, path), data=json.dumps(payload).encode("utf-8"), method=method)


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


def test_new_record_slug_skips_unusable_name_then_title():
    assert new_record("How I edit", "https://example.com/1", name="!!!")["name"] == "how-i-edit"
    record = new_record("你好", "https://example.com/1", site="tiktok", packet_id="01JABC12DEFG")
    assert record["image"] == "tiktok-01jabc12.png"


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
    assert packet_dir(tmp_path, record["id"], site="youtube") == directory


def test_write_packet_record_yaml_text(tmp_path):
    record = new_record(
        "Café edit", "https://example.com/v=1", site="youtube", timestamp_sec=12.5,
        tags=["b-roll"], notes="keep", packet_id="01JPIN0000000000000000000A",
        created_at="2026-10-08T00:00:00+00:00", name="My Still",
    )
    write_packet(record, PNG_1PX, root=tmp_path)
    assert (tmp_path / "youtube" / record["id"] / "record.yaml").read_text(encoding="utf-8") == (
        "id: 01JPIN0000000000000000000A\ncreated_at: '2026-10-08T00:00:00+00:00'\n"
        "title: Café edit\nsource_url: https://example.com/v=1\npage_url: https://example.com/v=1\n"
        "timestamp_sec: 12.5\nsite: youtube\ncapture_method: canvas\nimage: my-still.png\n"
        "name: my-still\ntags:\n- b-roll\nnotes: keep\nclipboard:\n  title: Café edit\n"
        "  url: https://example.com/v=1\n  text: 'Café edit\n\n    https://example.com/v=1'\n"
    )


def test_write_packet_unknown_site_falls_back_to_generic(tmp_path):
    record = new_record("Weird site", "https://example.com/1", site="../../etc")
    written = write_packet(record, PNG_1PX, root=tmp_path)
    assert written["site"] == "generic"
    assert (tmp_path / "generic" / record["id"] / "weird-site.png").exists()


def test_write_packet_reslugifies_the_record_name(tmp_path):
    record = new_record("Title", "https://example.com/1", site="youtube")
    written = write_packet(dict(record, name="  My Raw! Name  "), PNG_1PX, root=tmp_path)
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


def test_bad_record_yaml_raises_on_read_and_is_skipped_by_list(tmp_path):
    directory = tmp_path / "youtube" / "01JBAD"
    directory.mkdir(parents=True)
    (directory / "record.yaml").write_text("title: [unclosed\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        read_record("01JBAD", root=tmp_path)
    assert list_packets(root=tmp_path) == []


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
    header = "id,created_at,title,source_url,page_url,timestamp_sec,site,capture_method,name,image"
    assert csv_text.splitlines()[0] == header
    assert list(summaries[0]) == header.split(",")
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
        None,  # not a dict
        {"x": None, "y": 0, "width": 10, "height": 10},  # TypeError
        {"x": "a", "y": 0, "width": 10, "height": 10},  # ValueError
        {"x": float("nan"), "y": 0, "width": 10, "height": 10},  # non-finite
        {"x": -1000, "y": -1000, "width": 10, "height": 10},  # clamped below zero, degenerate
        {"x": 5000, "y": 5000, "width": 10, "height": 10},  # clamped past the image, degenerate
    ]
    for crop_rect in invalid_rects:
        assert apply_crop_rect(png, crop_rect) == png, f"rect {crop_rect!r}"


def test_apply_crop_rect_bad_dpr_falls_back_to_1():
    png = make_png_with_green_region()
    bad_dprs = [
        None,  # TypeError
        "abc",  # ValueError
        float("inf"),  # non-finite
        0,  # not positive
    ]
    for dpr in bad_dprs:
        cropped = apply_crop_rect(
            png, {"x": 0, "y": 0, "width": 10, "height": 10, "dpr": dpr}
        )
        assert png_size_and_pixel(cropped, (0, 0)) == ((10, 10), RED)


# --------------------------------------------------------------------------
# HTTP endpoints
# --------------------------------------------------------------------------

def test_health_endpoint(server):
    status, content_type, body = request(server_url(server, "/health"))
    payload = json.loads(body)
    assert status == 200
    assert payload == {"ok": True, "version": __version__}


def test_post_packet_json_and_list(server):
    status, _, body = request(
        server_url(server, "/packets"),
        data=json.dumps(make_payload(timestamp_sec="12.5")).encode("utf-8"),
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
    assert record["timestamp_sec"] == "12.5"  # a JSON body's timestamp_sec is stored as sent

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
    assert record["capture_method"] == "visible_tab"
    still = (tmp_path / record["site"] / record["id"] / record["image"]).read_bytes()
    assert png_size_and_pixel(still, (0, 0)) == ((40, 30), GREEN)
    # crop_rect is an ephemeral request field, never persisted.
    assert "crop_rect" not in read_record(record["id"], root=tmp_path)


def test_post_packet_visible_tab_without_crop_rect_keeps_full_still(server, tmp_path):
    png = make_png_with_green_region()
    payload = make_payload(capture_method="visible_tab", image_base64=base64.b64encode(png).decode("ascii"))
    status, _, body = send_json(server, "/packets", payload)
    assert status == 201
    record = json.loads(body)["packet"]
    still = (tmp_path / record["site"] / record["id"] / record["image"]).read_bytes()
    assert png_size_and_pixel(still, (0, 0)) == ((200, 100), RED)


def test_post_packet_multipart(server, tmp_path):
    crop_rect = {"x": 10, "y": 20, "width": 40, "height": 30, "dpr": 1}
    status, _, body = post_multipart_packet(
        server,
        image=make_png_with_green_region(),
        title="Multipart packet",
        source_url="https://example.com/multi",
        site="generic",
        crop_rect=json.dumps(crop_rect),
        timestamp_sec="7.5",
        tags='["a", "b"]',
    )
    assert status == 201
    packet = json.loads(body)["packet"]
    assert packet["title"] == "Multipart packet"
    assert packet["source_url"] == "https://example.com/multi"
    assert packet["image"] == "multipart-packet.png"
    # Form values are text: timestamp_sec and tags are parsed from it.
    assert (packet["timestamp_sec"], packet["tags"]) == (7.5, ["a", "b"])
    still = (tmp_path / packet["site"] / packet["id"] / packet["image"]).read_bytes()
    assert png_size_and_pixel(still, (0, 0)) == ((40, 30), GREEN)


def test_post_packet_multipart_bad_tags_json_400(server):
    response = post_multipart_packet(server, title="T", source_url="https://example.com/1", tags="[oops")
    assert (response[0], json.loads(response[2])["error"]) == (400, "Expecting value: line 1 column 2 (char 1)")


def test_post_packet_multipart_missing_title_400(server):
    response = post_multipart_packet(server, source_url="https://example.com/1")
    assert (response[0], json.loads(response[2])["error"]) == (400, "missing field: title")


def test_post_packet_json_missing_title_400(server):
    payload = make_payload()
    del payload["title"]
    response = send_json(server, "/packets", payload)
    assert (response[0], json.loads(response[2])["error"]) == (400, "missing field: title")


def test_post_packet_malformed_json_400(server):
    response = request(server_url(server, "/packets"), data=b"nope", method="POST")
    assert (response[0], json.loads(response[2])["error"]) == (400, "Expecting value: line 1 column 1 (char 0)")


def test_post_packet_other_content_type_415(server):
    response = request(
        server_url(server, "/packets"), data=b"x", method="POST", headers={"Content-Type": "text/plain"}
    )
    assert (response[0], json.loads(response[2])["error"]) == (415, "expected JSON or multipart/form-data")


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


def assert_not_found(status, body):
    assert (status, json.loads(body)) == (404, {"ok": False, "error": "not found"})


def test_get_unknown_route_404(server):
    status, _, body = request(server_url(server, "/nope"))
    assert_not_found(status, body)


def test_post_unknown_route_404(server):
    status, _, body = request(server_url(server, "/nope"), data=b"{}", method="POST")
    assert_not_found(status, body)


def test_put_unknown_route_404(server):
    status, _, body = request(server_url(server, "/nope"), data=b"{}", method="PUT")
    assert_not_found(status, body)


def test_get_error_outside_the_error_map_drops_the_connection(server, monkeypatch):
    # GET maps only FileNotFoundError (to 404); any other error gets no HTTP response.
    def fail(root):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("helper.server.list_packets", fail)
    with pytest.raises(http.client.RemoteDisconnected):
        urllib.request.urlopen(server_url(server, "/packets"), timeout=5)


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


def test_put_config_rejects_non_object_body(server):
    response = send_json(server, "/config", [], method="PUT")
    assert (response[0], json.loads(response[2])["error"]) == (400, "expected a JSON object")


def test_put_config_unwritable_config_file_400(server, tmp_path):
    # config.json is a symlink into a missing folder: it loads as {} but the
    # write raises FileNotFoundError (an OSError), for root too.
    config_path = tmp_path / "config.json"
    config_path.symlink_to(tmp_path / "missing" / "config.json")
    response = send_json(server, "/config", {"packet_root": str(tmp_path / "new-root")}, method="PUT")
    error = str(FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(config_path)))
    assert (response[0], json.loads(response[2])["error"]) == (400, error)


def test_put_config_unexpected_error_is_a_last_resort_500(server, tmp_path, monkeypatch):
    def fail(config, config_path):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("helper.server.save_config", fail)
    response = send_json(server, "/config", {"packet_root": str(tmp_path / "new-root")}, method="PUT")
    assert (response[0], json.loads(response[2])["error"]) == (500, "internal error: disk on fire")


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

@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_corrupt_config_raises_naming_the_file(tmp_path, content):
    config_path = tmp_path / "config.json"
    config_path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=str(config_path)):
        load_config(config_path)


@pytest.mark.parametrize("bad_root", [42, "", "relative/path"])
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


def test_cli_serve_reads_config_once_and_binds_the_resolved_root(monkeypatch, tmp_path, capsys):
    from helper import config
    from helper import server as server_module
    from helper.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLIPSTASH_PHOTOSHOP", "yes")
    config_path = tmp_path / "Clipstash" / "config.json"
    root = tmp_path / "configured"
    save_config({"packet_root": str(root)}, config_path)
    reads, served = [], []
    real_load_config = config.load_config
    monkeypatch.setattr(config, "load_config", lambda path=None: reads.append(path) or real_load_config(path))

    def stop_at_once(httpd, *args, **kwargs):
        served.append(httpd)
        raise KeyboardInterrupt

    monkeypatch.setattr(server_module.ThreadingHTTPServer, "serve_forever", stop_at_once)
    assert main(["--port", "0", "serve"]) == 0
    assert reads == [config_path]
    handler = served[0].RequestHandlerClass
    assert (handler.root, handler.photoshop_auto) == (str(root.resolve()), True)
    assert f"(root: {root.resolve()})" in capsys.readouterr().out


def test_cli_export_uses_root_given_before_or_after_the_command(monkeypatch, tmp_path, capsys):
    from helper.cli import main

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "packets"
    record = new_record("Exported", "https://example.com/v=1", site="youtube")
    write_packet(record, PNG_1PX, root=root)

    assert main(["--root", str(root), "export"]) == 0
    assert record["id"] in capsys.readouterr().out
    assert main(["export", "--root", str(root)]) == 0
    assert record["id"] in capsys.readouterr().out


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
    status, _, body = request(server_url(server, f"/picker/{session_id}/frame/2"))
    assert (status, json.loads(body)) == (404, {"ok": False, "error": "frame not found"})

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


def test_burst_multipart_415_creates_no_session(server):
    before = set(session_dir("x").parent.glob("*"))
    status, _, body = post_multipart_packet(
        server, path="/bursts", part="frame_0", title="T", source_url="https://example.com/1"
    )
    assert (status, json.loads(body)) == (415, {"ok": False, "error": "expected JSON or multipart/form-data"})
    assert set(session_dir("x").parent.glob("*")) == before


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


def test_burst_metadata_fallbacks(server):
    payload = make_burst_payload(frame_count=1, page_url="", site="", capture_method="", timestamp_sec="soon")
    session_id = json.loads(send_json(server, "/bursts", payload)[2])["session_id"]
    meta = load_session(session_id)
    assert (meta["page_url"], meta["site"], meta["capture_method"], meta["timestamp_sec"]) == (
        payload["source_url"], "generic", "burst_canvas", None
    )


def test_burst_list_timestamp_is_a_last_resort_500(server):
    response = send_json(server, "/bursts", make_burst_payload(timestamp_sec=[1]))
    error = "internal error: float() argument must be a string or a real number, not 'list'"
    assert (response[0], json.loads(response[2])["error"]) == (500, error)


def test_ffmpeg_burst_forces_capture_method_and_metadata_fallbacks(server, monkeypatch):
    monkeypatch.setattr("helper.server.ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr("helper.server.burst_frames_from_url", lambda *args, **kwargs: [PNG_1PX])
    payload = {"media_url": "https://example.com/v.mp4", "source_url": "https://example.com/s"}
    payload["capture_method"] = "burst_canvas"
    session_id = json.loads(send_json(server, "/bursts/ffmpeg", payload)[2])["session_id"]
    meta = load_session(session_id)
    assert (meta["capture_method"], meta["page_url"], meta["site"]) == (
        "burst_ffmpeg", payload["source_url"], "generic"
    )


def test_googlevideo_media_goes_to_ytdlp_on_a_generic_site(monkeypatch):
    calls = []
    for name in ("_download_with_ytdlp", "_download_with_urllib"):
        monkeypatch.setattr(ffmpeg_burst, name, lambda url, tmpdir, name=name: calls.append(name) or tmpdir)
    monkeypatch.setattr(ffmpeg_burst, "ytdlp_available", lambda: True)
    tmpdir = ffmpeg_burst.fetch_media_to_temp("https://rr1.googlevideo.com/videoplayback", site="generic")
    tmpdir.rmdir()  # governance: allow-delete DS001: empty mkdtemp dir the stubbed download returned
    assert calls == ["_download_with_ytdlp"]


def test_list_timestamp_becomes_none_in_create_burst_and_choose_frame(tmp_path):
    meta = create_burst([PNG_1PX], {"title": "t", "source_url": "https://example.com", "timestamp_sec": [1]})
    assert meta["timestamp_sec"] is None
    record = bursts.choose_frame(meta["session_id"], 0, {"timestamp_sec": [2]}, root=tmp_path)
    assert record["timestamp_sec"] is None


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
        data=json.dumps(make_burst_payload(frame_count=1, photoshop=True)).encode("utf-8"),
        method="POST",
    )
    assert status == 201
    session_id = json.loads(body)["session_id"]

    status, _, body = request(server_url(server, f"/picker/{session_id}"))
    assert status == 200
    html = body.decode("utf-8")
    assert 'new CustomEvent("clipstash:chosen"' in html
    assert "detail: payload.packet" in html
    # META is posted back to choose; the photoshop flag stays in the session.
    assert "photoshop" not in json.loads(re.search(r"const META = (.*);", html).group(1))


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
