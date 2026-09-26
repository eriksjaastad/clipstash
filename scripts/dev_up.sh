#!/usr/bin/env bash
# One-command clipstash helper dev environment.
#
# Creates .venv if it doesn't exist, installs the helper plus dev/test deps,
# and runs the helper in the foreground (Ctrl-C stops it).
#
# Usage:
#   ./scripts/dev_up.sh
set -euo pipefail

cd "$(dirname "$0")/.."

MIN_PYTHON="3.11"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python ${MIN_PYTHON}+ (https://www.python.org/downloads/macos/)." >&2
  exit 1
fi

python3 - "$MIN_PYTHON" <<'PY'
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

if [ ! -d .venv ]; then
  echo "creating .venv with $(python3 --version)…"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev]"

echo "starting clipstashd on http://127.0.0.1:8787 (Ctrl-C to stop)…"
exec python -m helper
