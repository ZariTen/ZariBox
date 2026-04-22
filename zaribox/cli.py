from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from . import __version__
from .commands.apply import run_apply
from .commands.destroy import run_destroy
from .commands.enter import run_enter
from .commands.list_cmd import run_list
from .commands.status import run_status
from .logging import CYN, GRN, RST, err

_COMMANDS: list[tuple[str, str, bool, Callable[..., int] | None]] = [
    ("status", "Show sync status with package drift", True, run_status),
    ("list", "List all ZariBox-managed containers", False, run_list),
    ("apply", "Sync container to match config", True, run_apply),
    ("enter", "Enter container (auto-apply if needed)", True, run_enter),
    ("destroy", "Remove container (home dir preserved)", True, run_destroy),
    ("help", "Show this help", False, None),
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zaribox", description="Declarative container manager"
    )
    subparsers = parser.add_subparsers(dest="command")
    for name, help_text, has_yaml, _ in _COMMANDS:
        sub = subparsers.add_parser(name, help=help_text)
        if has_yaml:
            sub.add_argument("yaml", nargs="?")
    return parser


def _print_help() -> None:
    print(f"\n{CYN}ZariBox{RST} v{__version__}  -- Declarative container manager\n")
    print("Usage:\n  zaribox <command> [file.yaml]\n")
    print("Commands:")
    for name, help_text, has_yaml, _ in _COMMANDS:
        arg = " [file.yaml]" if has_yaml else ""
        print(f"  {GRN}{name:<8}{RST}{arg:<14} {help_text}")
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    parsed_argv = list(argv) if argv is not None else sys.argv[1:]

    if not parsed_argv:
        _print_help()
        return 0

    args = parser.parse_args(parsed_argv)

    if args.command in (None, "help"):
        _print_help()
        return 0

    dispatch = {
        name: (has_yaml, handler) for name, _, has_yaml, handler in _COMMANDS if handler
    }

    if args.command not in dispatch:
        err(f"Unknown command: {args.command}")
        _print_help()
        return 1

    has_yaml, handler = dispatch[args.command]
    return handler(args.yaml) if has_yaml else handler()


if __name__ == "__main__":
    raise SystemExit(main())
