"""Helper configuration: on-disk ``CONFIG_FILENAME`` plus packet-root resolution.

The helper is the source of truth for the packet save root. The effective
root is resolved in this order:

    CLI ``--root``  ->  ``config.json`` ``packet_root``  ->  ``$CLIPSTASH_ROOT``
    ->  ``~/Clipstash/packets``

The config file lives at ``~/Clipstash/config.json`` (parent dir created on
demand) where ``config.json`` is the value of ``CONFIG_FILENAME``. Changing
the root never moves or deletes existing packets; new saves go to the new
root.

Public API
----------
``CONFIG_FILENAME``
    Name of the on-disk config file: ``config.json``.

``default_config_path()``
    Config file path: ``~/Clipstash/config.json``.

``load_config(config_path=None)``
    Load the helper config; tolerate missing/invalid files as ``{}``.

``save_config(config, config_path=None)``
    Persist the helper config, creating the parent directory as needed.

``default_packet_root()``
    Default save root with no overrides applied: ``~/Clipstash/packets``.

``normalize_packet_root(value)``
    Validate and normalize a user-supplied packet root (absolute or ``~``
    path; create-on-write, so the directory need not exist yet).

``effective_packet_root(cli_root=None, config_path=None)``
    Resolve the packet root by priority: CLI ``--root`` > config file
    ``packet_root`` > ``CLIPSTASH_ROOT`` > ``~/Clipstash/packets``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .packets import default_root

CONFIG_FILENAME = "config.json"


def default_config_path() -> Path:
    """Config file path: ~/Clipstash/config.json."""
    return Path.home() / "Clipstash" / CONFIG_FILENAME


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load the helper config; tolerate missing/invalid files as ``{}``."""
    path = Path(config_path) if config_path else default_config_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config: dict[str, Any], config_path: str | Path | None = None) -> None:
    """Persist the helper config, creating the parent directory as needed."""
    path = Path(config_path) if config_path else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def default_packet_root() -> Path:
    """Default save root with no overrides applied: ~/Clipstash/packets."""
    return (Path.home() / "Clipstash" / "packets").resolve()


def normalize_packet_root(value: Any) -> Path:
    """Validate and normalize a user-supplied packet root.

    Accepts an absolute path or ``~`` path, expands ``~``, and resolves to an
    absolute path. The directory does not need to exist yet (create-on-write).
    """
    if not isinstance(value, str):
        raise ValueError("packet_root must be a string")
    text = value.strip()
    if not text:
        raise ValueError("packet_root must not be empty")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("packet_root must be an absolute path")
    return path.resolve()


def effective_packet_root(
    cli_root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> Path:
    """Resolve the packet root by priority: CLI > config file > env > default."""
    if cli_root:
        return Path(cli_root).expanduser()
    config = load_config(config_path)
    configured = config.get("packet_root")
    if configured:
        try:
            return normalize_packet_root(configured)
        except ValueError:
            pass
    return default_root()
