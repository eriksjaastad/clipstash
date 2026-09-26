#!/usr/bin/env bash
# One-command clipstash helper dev environment.
#
# Stranger setup in one shot: clone the repo, then run this script. It creates
# .venv if it doesn't exist (Python 3.11+), installs the helper plus dev/test
# deps when the dependency spec changes, and runs the helper in the foreground
# (Ctrl-C stops it). Re-running skips the pip install when the virtualenv is
# already current, so it also works offline.
#
# Requirements: macOS 12+, Python 3.11+, Chrome. For the full setup story
# (venv + `pip install -e .` or `uv sync`, running via `python -m helper` /
# `clipstashd`, loading the unpacked extension/, changing the save root) see
# `clipstashd --help`. LaunchAgent install/unload steps live in the comments of
# packaging/macos/com.clipstash.helper.plist.
#
# Usage:
#   ./scripts/dev_up.sh
set -euo pipefail

cd "$(dirname "$0")/.."

MIN_PYTHON="3.11"
STAMP=".venv/.clipstash-deps"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python ${MIN_PYTHON}+ (https://www.python.org/downloads/macos/)." >&2
  exit 1
fi

check_python_version() {
  "$1" - "$MIN_PYTHON" <<'PY'
import sys

required = tuple(int(part) for part in sys.argv[1].split("."))
if sys.version_info[:2] < required:
    print(
        f"clipstash needs Python {'.'.join(map(str, required))}+; "
        f"found {sys.version.split()[0]}",
        file=sys.stderr,
    )
    sys.exit(1)
PY
}

check_python_version python3

if [ ! -x .venv/bin/python ]; then
  echo "creating .venv with $(python3 --version)…"
  python3 -m venv .venv
fi

# The venv may predate the Python 3.11 floor; fail with a clear fix rather
# than activating an incompatible interpreter.
.venv/bin/python - "$MIN_PYTHON" <<'PY'
import sys

required = tuple(int(part) for part in sys.argv[1].split("."))
if sys.version_info[:2] < required:
    print(
        f"existing .venv uses Python {sys.version.split()[0]}; recreate it: "
        f"rm -rf .venv && ./scripts/dev_up.sh",
        file=sys.stderr,
    )
    sys.exit(1)
PY

# shellcheck disable=SC1091
source .venv/bin/activate

deps_stamp() {
  shasum -a 256 pyproject.toml 2>/dev/null | awk '{print $1}'
}

DEPS_STAMP="$(deps_stamp)"

install_deps() {
  echo "installing helper + dev deps…"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -e ".[dev]"
  printf '%s' "$DEPS_STAMP" > "$STAMP"
}

venv_ready() {
  # Everything the launcher would have installed must live in this venv
  # (not somewhere earlier on PATH) before we stamp it as current.
  [ -n "${VIRTUAL_ENV:-}" ] &&
    [ -x "${VIRTUAL_ENV}/bin/clipstashd" ] &&
    [ -x "${VIRTUAL_ENV}/bin/pytest" ] &&
    python -c "import helper" >/dev/null 2>&1
}

if [ ! -f "$STAMP" ]; then
  # Transition from a venv created before the stamp existed: reuse an
  # already-installed, importable helper without touching the package index,
  # so the first offline re-run still starts.
  if venv_ready; then
    printf '%s' "$DEPS_STAMP" > "$STAMP"
  else
    install_deps
  fi
elif [ "$(cat "$STAMP")" != "$DEPS_STAMP" ]; then
  install_deps
fi

echo "starting clipstashd on http://127.0.0.1:8787 (Ctrl-C to stop)…"
exec python -m helper
