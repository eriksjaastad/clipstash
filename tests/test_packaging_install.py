"""Guard packaging drift: brew formula + binary install scripts stay wired to the repo.

These are drift guards, not install tests: they check that the packaging files
exist, carry the right entry points, and that ``helper.cli``'s docstring still
points strangers at them. They never run ``brew install`` or produce a binary.
"""

from __future__ import annotations

from pathlib import Path

from helper import cli

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"{rel} must exist"
    return path.read_text(encoding="utf-8")


def test_homebrew_formula_exists_and_wires_clipstashd() -> None:
    formula = _read("packaging/homebrew/clipstash.rb")
    assert "class Clipstash" in formula
    assert "clipstashd" in formula
    assert "Virtualenv" in formula or "virtualenv_create" in formula
    assert "com.clipstash.helper" in formula


def test_binary_build_and_install_scripts_exist_and_are_executable() -> None:
    build = _read("scripts/build_macos_binary.sh")
    assert "PyInstaller" in build or "pyinstaller" in build

    install = _read("scripts/install_macos_helper.sh")
    assert "LaunchAgents" in install

    for rel in ("scripts/build_macos_binary.sh", "scripts/install_macos_helper.sh"):
        path = ROOT / rel
        assert path.is_file(), f"{rel} must exist"
        assert path.stat().st_mode & 0o111, f"{rel} should have its executable bit set"


def test_cli_docstring_points_strangers_at_brew_and_binary_paths() -> None:
    doc = cli.__doc__ or ""
    assert "brew" in doc or "Homebrew" in doc
    assert "install_macos_helper" in doc or "build_macos_binary" in doc
