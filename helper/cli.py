"""Command-line entry points for the clipstash helper."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .config import effective_packet_root
from .packets import export_csv

_EPILOG = """\
setup
-----
Requires macOS 12+, Python 3.11+, and Chrome (for the unpacked extension).

  python3 -m venv .venv && source .venv/bin/activate
  pip install -e .        # or: uv sync  (then prefix commands with `uv run`)

Run the helper:
  python -m helper        # module form, no install needed
  clipstashd              # installed entry point (default command: serve)
  ./scripts/dev_up.sh     # one-command dev env: venv + deps + serve

Health check:
  curl http://127.0.0.1:8787/health

Extension:
  chrome://extensions → Developer mode → Load unpacked → choose extension/.

Save root:
  Default ~/Clipstash/packets. Change it from the extension Options page
  (writes ~/Clipstash/config.json) or with --root / CLIPSTASH_ROOT.

Troubleshooting:
  python3 not found         install Python 3.11+ (python.org or Homebrew)
  port 8787 already in use  another clipstashd is running; stop it first
  popup shows not running   check http://127.0.0.1:8787/health in Chrome and
                            that no firewall blocks loopback
  clipstashd not found      activate the venv, or use `uv run clipstashd`
                            after `uv sync`
"""


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clipstashd",
        description="Local clipstash helper: write packets and serve the extension.",
        epilog=_EPILOG,
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
        help="packet root directory (default ~/Clipstash/packets or $CLIPSTASH_ROOT)",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="run the HTTP server (default)")
    serve_parser.set_defaults(command="serve")
    serve_parser.add_argument(
        "--photoshop",
        action="store_true",
        default=_env_bool("CLIPSTASH_PHOTOSHOP"),
        help="place every saved packet into Photoshop after write (macOS only; default $CLIPSTASH_PHOTOSHOP)",
    )

    export_parser = subparsers.add_parser("export", help="print packets as CSV and exit")
    export_parser.add_argument("--root", default=None, help="packet root directory")
    export_parser.set_defaults(command="export")

    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "export":
        sys.stdout.write(export_csv(effective_packet_root(getattr(args, "root", None))))
        return 0

    from .server import run_server

    run_server(
        host=args.host,
        port=args.port,
        root=effective_packet_root(getattr(args, "root", None)),
        photoshop_auto=getattr(args, "photoshop", False),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
