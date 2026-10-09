"""Helper configuration: on-disk ``CONFIG_FILENAME`` plus packet-root resolution.

The helper is the source of truth for the packet save root. The effective
root is resolved in this order:

    CLI ``--root``  ->  ``config.json`` ``packet_root``  ->  ``$CLIPSTASH_ROOT``
    ->  ``~/Clipstash/packets``

The config file lives at ``~/Clipstash/config.json`` (parent dir created on
demand) where ``config.json`` is the value of ``CONFIG_FILENAME``. Changing
the root never moves or deletes existing packets; new saves go to the new
root.

A missing config file means "nothing configured yet". A config file that
exists but cannot be used (unreadable, invalid JSON, not a JSON object, or a
``packet_root`` that ``normalize_packet_root`` rejects) raises ``ConfigError``
naming the file, rather than silently saving packets to a different root.

Public API
----------
``CONFIG_FILENAME``
``ConfigError``
``default_config_path()``
``load_config()``
``save_config()``
``default_packet_root()``
``normalize_packet_root()``
``effective_packet_root()``
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .packets import default_root

CONFIG_FILENAME = "config.json"


class ConfigError(ValueError):
    """The config file exists but is unusable; the message names the file."""


def default_config_path() -> Path:
    """Config file path: ~/Clipstash/config.json."""
    return Path.home() / "Clipstash" / CONFIG_FILENAME


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load the helper config.

    A missing file is ``{}`` (nothing configured yet). An unreadable file,
    invalid JSON, or a top-level value that is not an object raises
    ``ConfigError`` naming the file.
    """
    path = Path(config_path) if config_path else default_config_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:  # governance: allow-silent SF002: no config file yet; {} means nothing configured and effective_packet_root falls through to $CLIPSTASH_ROOT or the default root
        return {}
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError and UnicodeDecodeError
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(
            f"config file {path} must contain a JSON object, not {type(data).__name__}"
        )
    return data


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
    """Resolve the packet root by priority: CLI > config file > env > default.

    Raises ``ConfigError`` when the config file is unusable or holds a
    ``packet_root`` that ``normalize_packet_root`` (the ``PUT /config``
    validation) rejects; it never falls back past a configured root.
    """
    if cli_root:
        return Path(cli_root).expanduser()
    path = Path(config_path) if config_path else default_config_path()
    config = load_config(path)
    if "packet_root" in config:
        try:
            return normalize_packet_root(config["packet_root"])
        except ValueError as exc:
            raise ConfigError(f"invalid packet_root in config file {path}: {exc}") from exc
    return default_root()
