"""Shared fixtures: a helper HTTP server rooted in tmp_path with its own config.json."""

from __future__ import annotations

import threading

import pytest

from helper.server import create_server


@pytest.fixture()
def start_server(tmp_path):
    """Start a server on a free port; extra kwargs go to create_server."""
    started = []

    def _start(**kwargs):
        httpd = create_server(
            host="127.0.0.1",
            port=0,
            root=str(tmp_path),
            config_path=tmp_path / "config.json",
            **kwargs,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append((httpd, thread))
        return httpd

    yield _start

    for httpd, thread in started:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


@pytest.fixture()
def server(start_server):
    return start_server()
