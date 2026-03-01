from __future__ import annotations

import argparse
from typing import Sequence

from . import __version__
from .commands.list_cmd import run_list
from .commands.status import run_status
from .logging import CYN, GRN, RST, err


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zaribox",
        add_help=False,
        description="Declarative container manager",
    )
    parser.add_argument("command", nargs="?", default="help")
    parser.add_argument("yaml", nargs="?")
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
    print(f"  {GRN}apply{RST}    [file.yaml]   (phase 2) not implemented yet")
    print(f"  {GRN}enter{RST}    [file.yaml]   (phase 2) not implemented yet")
    print(f"  {GRN}destroy{RST}  [file.yaml]   (phase 2) not implemented yet")
    print(f"  {GRN}help{RST}                   Show this help")
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command

    if command in {"help", "-h", "--help"}:
        _print_help()
        return 0

    if command == "status":
        return run_status(args.yaml)

    if command == "list":
        return run_list()

    if command in {"apply", "enter", "destroy"}:
        err(f"Command '{command}' is not implemented in Phase 1 yet.")
        return 2

    err(f"Unknown command: {command}")
    _print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
