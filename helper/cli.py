"""clipstashd — local clipstash helper: write packets and serve the extension.

clipstashd is the local helper for the clipstash Chrome extension. It
writes video-still packets to disk, serves the HTTP API the extension
talks to, and can optionally hand every saved still to Photoshop (macOS
only).

Commands
--------
serve (default)
    Run the HTTP server for the extension. ``serve`` is the default
    command: running ``clipstashd`` with no command is the same as
    ``clipstashd serve``.

    ``serve --photoshop``
        Place every saved packet into Photoshop after write (macOS only;
        default ``$CLIPSTASH_PHOTOSHOP``).

export
    Print packets as CSV to stdout and exit.

    ``export --root``
        Packet root directory (default ``config.json``, then
        ``$CLIPSTASH_ROOT``, then ``~/Clipstash/packets``).

Options
-------
``--host``
    Bind address (loopback only; default 127.0.0.1).
``--port``
    Port to listen on (default 8787 or ``$CLIPSTASH_PORT``).
``--root``
    Packet root directory (default ``config.json``, then
    ``$CLIPSTASH_ROOT``, then ``~/Clipstash/packets``).
``--version``
    Print the clipstashd version and exit.

Environment
-----------
CLIPSTASH_PORT      default for ``--port`` when set.
CLIPSTASH_ROOT      default packet root when set.
CLIPSTASH_PHOTOSHOP enable auto-place when set to 1/true/yes/on, same as
                    ``serve --photoshop``.

Save root
---------
Packets default to ``~/Clipstash/packets/<site>/<id>/<slug>.png`` +
``record.yaml`` (``<site>`` = youtube | tiktok | instagram | x | generic;
``<slug>`` = slugified video title or popup type-in). Change the root from the
extension Options page (writes ``~/Clipstash/config.json``) or with
``--root`` / ``CLIPSTASH_ROOT``. If ``config.json`` exists but is unreadable,
is not valid JSON, or holds an invalid ``packet_root``, clipstashd prints an
error naming the file and exits 1 instead of saving somewhere else.

Setup
-----
Install on macOS with Homebrew (packaging/homebrew/README.md) or as a single
binary (scripts/build_macos_binary.sh, then scripts/install_macos_helper.sh).
README.md covers both and loading the extension. Developer path (macOS 12+,
Python 3.11+):

  python3 -m venv .venv && source .venv/bin/activate
  pip install -e .        # or: uv sync  (then prefix commands with `uv run`)
  clipstashd              # or: python -m helper, or ./scripts/dev_up.sh
  curl http://127.0.0.1:8787/health

Native burst (cross-origin video)
---------------------------------
Cross-origin video (e.g. googlevideo on YouTube) is captured through a
helper-native ffmpeg burst from the video's media URL. Install ffmpeg on
macOS with ``brew install ffmpeg``. YouTube signed media URLs download best
with ``yt-dlp`` on PATH (``brew install yt-dlp``). Both are optional system
tools; when they are missing the extension falls back to a single
visible-tab still.

Troubleshooting
---------------
  python3 not found         install Python 3.11+ (python.org or Homebrew)
  port 8787 already in use  another clipstashd is running; stop it first
  popup shows not running   check http://127.0.0.1:8787/health in Chrome and
                            that no firewall blocks loopback
  clipstashd not found      activate the venv, or use `uv run clipstashd`
                            after `uv sync`
  native burst unavailable  brew install ffmpeg (and optionally yt-dlp) so
                            the helper can extract frames from media URLs
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .coerce import env_flag
from .config import ConfigError, effective_packet_root
from .packets import export_csv


def build_parser() -> argparse.ArgumentParser:
    """Build the clipstashd CLI parser. The module docstring is the description."""
    parser = argparse.ArgumentParser(
        prog="clipstashd",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"clipstashd {__version__}")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (loopback only; default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CLIPSTASH_PORT", "8787")),
        help="port to listen on (default 8787 or $CLIPSTASH_PORT)",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="packet root directory (default config.json, then $CLIPSTASH_ROOT, then ~/Clipstash/packets)",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="run the HTTP server (default)")
    serve_parser.set_defaults(command="serve")
    serve_parser.add_argument(
        "--photoshop",
        action="store_true",
        default=env_flag("CLIPSTASH_PHOTOSHOP"),
        help="place every saved packet into Photoshop after write (macOS only; default $CLIPSTASH_PHOTOSHOP)",
    )

    export_parser = subparsers.add_parser("export", help="print packets as CSV and exit")
    # SUPPRESS: an omitted subcommand --root must not overwrite a global --root.
    export_parser.add_argument("--root", default=argparse.SUPPRESS, help="packet root directory")
    export_parser.set_defaults(command="export")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "serve"

    try:
        root = effective_packet_root(getattr(args, "root", None))
    except ConfigError as exc:
        sys.stderr.write(f"clipstashd: {exc}\n")
        return 1

    if command == "export":
        sys.stdout.write(export_csv(root))
        return 0

    from .server import run_server

    run_server(
        host=args.host,
        port=args.port,
        root=root,
        photoshop_auto=getattr(args, "photoshop", False),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
