"""Command-line entry points for the clipstash helper."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .packets import export_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clipstashd",
        description="Local clipstash helper: write packets and serve the extension.",
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

    export_parser = subparsers.add_parser("export", help="print packets as CSV and exit")
    export_parser.add_argument("--root", default=None, help="packet root directory")
    export_parser.set_defaults(command="export")

    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "export":
        sys.stdout.write(export_csv(getattr(args, "root", None)))
        return 0

    from .server import run_server

    run_server(host=args.host, port=args.port, root=args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
