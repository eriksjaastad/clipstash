#!/usr/bin/env bash
# Build a single-file, unsigned macOS binary of clipstashd with PyInstaller.
#
# Output: dist/clipstashd (console, onefile). dist/ and build/ are gitignored,
# so the binary is never committed. Signing/notarization are out of scope.
#
# Usage:
#   ./scripts/build_macos_binary.sh
#
# PyInstaller is a build-only dependency: it is installed ad hoc into the
# existing .venv (or a throwaway build/build-venv when .venv is absent) and is
# not part of the pyproject.toml runtime dependencies.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "build_macos_binary.sh produces a macOS binary; run it on macOS." >&2
  exit 1
fi

mkdir -p "$ROOT/build"

if [ -x "$ROOT/.venv/bin/python" ]; then
  BUILD_PY="$ROOT/.venv/bin/python"
  if ! "$BUILD_PY" -c "import helper, yaml, PIL" >/dev/null 2>&1; then
    echo "installing helper + runtime deps into .venv…"
    "$BUILD_PY" -m pip install --quiet --disable-pip-version-check -e .
  fi
else
  BUILD_PY="$ROOT/build/build-venv/bin/python"
  if [ ! -x "$BUILD_PY" ]; then
    echo "creating throwaway build venv at build/build-venv…"
    python3 -m venv "$ROOT/build/build-venv"
  fi
  echo "installing helper + runtime deps into build venv…"
  "$BUILD_PY" -m pip install --quiet --disable-pip-version-check .
fi

echo "installing pyinstaller (build-only)…"
"$BUILD_PY" -m pip install --quiet --disable-pip-version-check pyinstaller

# PyInstaller needs a top-level entry script: passing helper/__main__.py
# directly bundles it as a script and its relative import fails at runtime.
cat > "$ROOT/build/pyinstaller_entry.py" <<'PY'
from helper.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
PY

echo "building onefile console binary…"
"$BUILD_PY" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --console \
  --name clipstashd \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build" \
  --specpath "$ROOT/build" \
  --paths "$ROOT" \
  "$ROOT/build/pyinstaller_entry.py"

echo "built: $ROOT/dist/clipstashd"
