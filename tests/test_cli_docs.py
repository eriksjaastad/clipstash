"""Guard helper.cli's module docstring against CLI drift.

build_parser() uses the module docstring as its description, so these tests
pin the two to each other: every parser option (including subparser options)
must be documented, every flag-like token in the docstring must be a real
parser option, and --help must open with the docstring's first line.
"""

from __future__ import annotations

import argparse
import re

from helper import cli

_DOC_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")

# Env-style tokens are written as ``$CLIPSTASH_PORT`` / ``CLIPSTASH_ROOT``
# without a leading ``--``, so the docstring needs no allowlist for them.
# Keep this empty: a new ``--token`` in the docstring should be a real option.
_ALLOWLIST: frozenset[str] = frozenset()


def _doc_flag_tokens(doc: str) -> list[str]:
    return _DOC_FLAG_RE.findall(doc)


def _walk_parser_actions(parser: argparse.ArgumentParser):
    """Yield every action in *parser*, descending into subparsers."""
    for action in parser._actions:
        yield action
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                yield from _walk_parser_actions(subparser)


def _parser_option_strings() -> set[str]:
    return {
        option
        for action in _walk_parser_actions(cli.build_parser())
        for option in action.option_strings
    }


def test_every_parser_option_appears_in_module_docstring() -> None:
    options = {
        option
        for action in _walk_parser_actions(cli.build_parser())
        for option in action.option_strings
        if option not in ("-h", "--help")
    }
    assert options, "expected CLI options beyond --help"
    for option in sorted(options):
        assert option in cli.__doc__, (
            f"{option} is defined by build_parser() but missing from helper.cli.__doc__"
        )


def test_every_documented_flag_is_a_real_parser_option() -> None:
    real = _parser_option_strings()
    tokens = _doc_flag_tokens(cli.__doc__ or "")
    assert tokens, "expected --flag tokens in helper.cli.__doc__"
    for token in tokens:
        assert token in real or token in _ALLOWLIST, (
            f"{token} appears in helper.cli.__doc__ but is not a build_parser() option"
        )


def test_help_contains_docstring_first_line() -> None:
    assert cli.__doc__ is not None
    first_line = cli.__doc__.strip().splitlines()[0].strip()
    assert first_line, "module docstring must have a non-empty first line"
    assert first_line in cli.build_parser().format_help()
