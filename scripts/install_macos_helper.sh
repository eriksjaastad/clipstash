#!/usr/bin/env bash
# Install a prebuilt dist/clipstashd single binary and load the LaunchAgent.
#
# Usage:
#   ./scripts/build_macos_binary.sh   # produces dist/clipstashd
#   ./scripts/install_macos_helper.sh
#
# Environment:
#   CLIPSTASHD_BIN    path to a prebuilt clipstashd binary
#                     (default: dist/clipstashd)
#   CLIPSTASH_PREFIX  install directory (default: $HOME/bin)
#
# The script installs the binary, writes
# ~/Library/LaunchAgents/com.clipstash.helper.plist with ProgramArguments
# pointing at the installed path (substituting the @@CLIPSTASHD@@ placeholder),
# then reloads the LaunchAgent via launchctl.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${CLIPSTASH_PREFIX:-$HOME/bin}"
SRC_BIN="${CLIPSTASHD_BIN:-$ROOT/dist/clipstashd}"

if [ ! -x "$SRC_BIN" ]; then
  echo "clipstashd binary not found at $SRC_BIN" >&2
  echo "Run ./scripts/build_macos_binary.sh first, or set CLIPSTASHD_BIN=/path/to/clipstashd" >&2
  exit 1
fi

mkdir -p "$PREFIX"
cp "$SRC_BIN" "$PREFIX/clipstashd"
chmod +x "$PREFIX/clipstashd"

LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST="$LAUNCH_AGENTS/com.clipstash.helper.plist"
mkdir -p "$LAUNCH_AGENTS"
awk -v bin="$PREFIX/clipstashd" \
  '{ gsub(/@@CLIPSTASHD@@/, bin); print }' \
  "$ROOT/packaging/macos/com.clipstash.helper.plist" > "$PLIST"

launchctl bootout "gui/$(id -u)/com.clipstash.helper" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "installed $PREFIX/clipstashd"
echo "LaunchAgent loaded ($PLIST)"
echo "health check:"
echo "  curl http://127.0.0.1:8787/health"
