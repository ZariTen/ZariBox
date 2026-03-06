from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .commands.apply import run_apply
from .commands.destroy import run_destroy
from .commands.enter import run_enter
from .commands.list_cmd import run_list
from .commands.status import run_status
from .logging import CYN, GRN, RST, err


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zaribox",
        description="Declarative container manager",
    )

    subparsers = parser.add_subparsers(dest="command")

    status_parser = subparsers.add_parser(
        "status", help="Show sync status with package drift"
    )
    status_parser.add_argument("yaml", nargs="?")

    apply_parser = subparsers.add_parser("apply", help="Sync container to match config")
    apply_parser.add_argument("yaml", nargs="?")

    enter_parser = subparsers.add_parser(
        "enter", help="Enter container (auto-apply if needed)"
    )
    enter_parser.add_argument("yaml", nargs="?")

    destroy_parser = subparsers.add_parser(
        "destroy", help="Remove container (home dir preserved)"
    )
    destroy_parser.add_argument("yaml", nargs="?")

    subparsers.add_parser("list", help="List all ZariBox-managed containers")
    subparsers.add_parser("help", help="Show this help")

    return parser


def _print_help() -> None:
    print()
    print(f"{CYN}ZariBox{RST} v{__version__}  -- Declarative container manager")
    print()
    print("Usage:")
    print("  zaribox <command> [file.yaml]")
    print()
    print("Commands:")
    print(f"  {GRN}status{RST}   [file.yaml]   Show sync status with package drift")
    print(f"  {GRN}list{RST}                   List all ZariBox-managed containers")
    print(f"  {GRN}apply{RST}    [file.yaml]   Sync container to match config")
    print(f"  {GRN}enter{RST}    [file.yaml]   Enter container (auto-apply if needed)")
    print(f"  {GRN}destroy{RST}  [file.yaml]   Remove container (home dir preserved)")
    print(f"  {GRN}help{RST}                   Show this help")
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    parsed_argv = list(argv) if argv is not None else sys.argv[1:]

    if not parsed_argv:
        _print_help()
        return 0

    args = parser.parse_args(parsed_argv)
    command = args.command

    if command == "help":
        _print_help()
        return 0

    if command == "status":
        return run_status(args.yaml)

    if command == "list":
        return run_list()

    if command == "apply":
        return run_apply(args.yaml)

    if command == "enter":
        return run_enter(args.yaml)

    if command == "destroy":
        return run_destroy(args.yaml)

    err(f"Unknown command: {command}")
    _print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
