from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from . import __version__
from .commands.create import run_create
from .commands.destroy import run_destroy
from .commands.enter import run_enter
from .commands.list_cmd import run_list
from .commands.pull import run_pull
from .commands.status import run_status
from .commands.sync import run_sync
from .logging import CYN, GRN, RST, err

_COMMANDS: list[tuple[str, str, str | None, Callable[..., int]]] = [
    ("create", "Create a new container", "file.yaml", run_create),
    ("status", "Show sync status with package drift", "container", run_status),
    ("list", "List all ZariBox-managed containers", None, run_list),
    ("sync", "Sync container to match config", "container", run_sync),
    ("pull", "Sync packages from container into config file", "container", run_pull),
    ("enter", "Enter container (auto-apply if needed)", "container", run_enter),
    ("destroy", "Remove container (home dir preserved)", "container", run_destroy),
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zaribox", description="Declarative container manager", add_help=False
    )
    subparsers = parser.add_subparsers(dest="command")

    for name, help_text, arg_name, _ in _COMMANDS:
        sub = subparsers.add_parser(name, help=help_text, add_help=False)
        if arg_name:
            var_name = arg_name.replace(".", "_")
            sub.add_argument(var_name, nargs=None if name != "create" else "?")

    return parser


def _print_help() -> None:
    print(f"\n{CYN}ZariBox{RST} v{__version__}  -- Declarative container manager\n")
    print("Usage:\n  zaribox <command> [args]\n")
    print("Commands:")
    for name, help_text, arg_name, _ in _COMMANDS:
        arg = f" [{arg_name}]" if arg_name else ""
        print(f"  {GRN}{name:<8}{RST}{arg:<14} {help_text}")
    print(f"  {GRN}{'help':<8}{RST}{'':<14} Show this help")
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parsed_argv = list(argv) if argv is not None else sys.argv[1:]

    if not parsed_argv or parsed_argv[0] in ("help", "-h", "--help"):
        _print_help()
        return 0

    parser = _build_parser()

    try:
        args = parser.parse_args(parsed_argv)
    except SystemExit:
        return 1

    dispatch = {name: (arg_name, handler) for name, _, arg_name, handler in _COMMANDS}

    if args.command not in dispatch:
        err(f"Unknown command: {args.command}")
        return 1

    arg_name, handler = dispatch[args.command]

    if arg_name:
        var_name = arg_name.replace(".", "_")
        value = getattr(args, var_name)
        return handler(value)

    return handler()


if __name__ == "__main__":
    raise SystemExit(main())
