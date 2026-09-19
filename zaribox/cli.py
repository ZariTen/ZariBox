from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn

from . import __version__
from .commands.enter import run_enter
from .logging import err, log, set_color_enabled
from .service import PlanResult, ZariBoxService


class _ArgumentParser(argparse.ArgumentParser):
    def __init__(
        self, *args: object, json_errors: bool = False, **kwargs: object
    ) -> None:
        self.json_errors = json_errors
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def error(self, message: str) -> NoReturn:
        if self.json_errors:
            raise ValueError(message)
        super().error(message)


def _parser(*, json_errors: bool = False) -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="zaribox",
        description="Declarative Podman environments",
        json_errors=json_errors,
    )
    parser.add_argument(
        "--version", action="version", version=f"ZariBox v{__version__}"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit one machine-readable JSON document"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="disable ANSI color output"
    )
    subparsers = parser.add_subparsers(dest="command")

    validate = subparsers.add_parser(
        "validate", help="validate and normalize a manifest"
    )
    validate.add_argument("config", nargs="?")

    plan = subparsers.add_parser(
        "plan", help="show reconciliation actions without changing anything"
    )
    plan.add_argument("target", nargs="?")

    ensure = subparsers.add_parser("ensure", help="create or reconcile a container")
    ensure.add_argument("config", nargs="?")
    ensure.add_argument(
        "--force", action="store_true", help="allow destructive reconciliation"
    )
    ensure.add_argument("--lock-timeout", type=float, default=30.0)

    create = subparsers.add_parser("create", help="create or recreate from a manifest")
    create.add_argument("config", nargs="?")
    create.add_argument(
        "--force",
        action="store_true",
        help="accepted for consistency; create already recreates",
    )

    apply_parser = subparsers.add_parser("apply", help="reconcile a managed container")
    apply_parser.add_argument("target")
    apply_parser.add_argument("--force", action="store_true")

    inspect_parser = subparsers.add_parser(
        "inspect", help="return structured container state"
    )
    inspect_parser.add_argument("target")
    status = subparsers.add_parser("status", help="alias for inspect")
    status.add_argument("target")

    execute = subparsers.add_parser(
        "exec", help="execute a bounded non-interactive command"
    )
    execute.add_argument("target")
    execute.add_argument("--timeout", type=float, default=300.0)
    execute.add_argument("--max-output-bytes", type=int, default=1_048_576)
    execute.add_argument("--workdir")
    execute.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    execute.add_argument("--root", action="store_true")
    execute.add_argument("--shell", help="explicitly run a shell command")
    execute.add_argument("argv", nargs="*")

    enter = subparsers.add_parser("enter", help="open an interactive shell")
    enter.add_argument("target")

    export = subparsers.add_parser(
        "export", help="merge manually installed packages into the manifest"
    )
    export.add_argument("target")

    subparsers.add_parser("list", help="list managed containers")

    remove = subparsers.add_parser(
        "remove", aliases=["destroy"], help="destroy a container, preserving its home"
    )
    remove.add_argument("target")
    remove.add_argument(
        "--force", action="store_true", help="skip interactive confirmation"
    )

    subparsers.add_parser(
        "cleanup", help="remove expired agent boxes and stale operation leases"
    )
    subparsers.add_parser("mcp", help="run the optional local MCP server over stdio")
    return parser


def _move_global_options(args: list[str]) -> list[str]:
    """Allow global formatting options before a command's ``--`` delimiter."""
    try:
        delimiter = args.index("--")
    except ValueError:
        delimiter = len(args)
    prefix = args[:delimiter]
    suffix = args[delimiter:]
    globals_found = [arg for arg in prefix if arg in {"--json", "--no-color"}]
    remainder = [arg for arg in prefix if arg not in {"--json", "--no-color"}]
    return [*globals_found, *remainder, *suffix]


def _json_envelope(
    command: str, *, data: object = None, error: str | None = None
) -> str:
    value = {
        "schema_version": 1,
        "ok": error is None,
        "command": command,
        "data": data,
        "error": ({"code": "operation_failed", "message": error} if error else None),
    }
    return json.dumps(value, sort_keys=True)


def _print_data(command: str, data: object, json_mode: bool) -> None:
    if json_mode:
        print(_json_envelope(command, data=data))
        return
    if isinstance(data, dict):
        print(json.dumps(data, indent=2, sort_keys=True))
    elif isinstance(data, list):
        for item in data:
            print(item)
    else:
        print(data)


def _environment(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Environment value must be KEY=VALUE: {value}")
        key, item = value.split("=", 1)
        if not key:
            raise ValueError("Environment key must not be empty")
        result[key] = item
    return result


def _plan(service: ZariBoxService, target: str | None) -> PlanResult:
    if target is None or Path(target).expanduser().is_file():
        return service.plan(target)
    return service.plan_target(target)


def _confirm_destroy(name: str) -> bool:
    print(f"This will destroy container '{name}' (home directory is preserved).")
    try:
        return input("  Confirm? [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    delimiter = raw_args.index("--") if "--" in raw_args else len(raw_args)
    json_requested = "--json" in raw_args[:delimiter]
    parser = _parser(json_errors=json_requested)
    try:
        args = parser.parse_args(_move_global_options(raw_args))
    except ValueError as exc:
        set_color_enabled(False)
        print(_json_envelope("parse", error=str(exc)))
        return 2
    json_mode = bool(getattr(args, "json", False))
    no_color = (
        bool(getattr(args, "no_color", False)) or json_mode or "NO_COLOR" in os.environ
    )
    set_color_enabled(not no_color)

    if args.command is None:
        parser.print_help()
        return 0

    service = ZariBoxService()
    command = str(args.command)
    try:
        if command == "mcp":
            if json_mode:
                raise ValueError("MCP stdio transport cannot be combined with --json")
            from .mcp_server import main as run_mcp

            return run_mcp()

        if command == "validate":
            config = service.validate(args.config)
            data: dict[str, Any] = {
                "valid": True,
                "config_path": str(config.file_path.resolve()),
                "name": config.name,
                "image": config.image,
                "kind": config.kind or "LegacyBox",
                "security_profile": service._profile(config),
            }
            _print_data(command, data, json_mode)
            return 0

        if command == "plan":
            result = _plan(service, args.target)
            _print_data(command, asdict(result), json_mode)
            return 0

        if command in {"ensure", "create"}:
            recreate = command == "create"
            result = service.ensure(
                args.config,
                force=bool(args.force) or recreate,
                recreate=recreate,
                lock_timeout=getattr(args, "lock_timeout", 30.0),
            )
            _print_data(command, asdict(result), json_mode)
            return 0

        if command == "apply":
            config = service.config_for_target(args.target)
            result = service.ensure(config.file_path, force=args.force)
            _print_data(command, asdict(result), json_mode)
            return 0

        if command in {"inspect", "status"}:
            result = service.inspect(args.target)
            _print_data(command, asdict(result), json_mode)
            return 0

        if command == "exec":
            argv_value = list(args.argv)
            if argv_value and argv_value[0] == "--":
                argv_value.pop(0)
            if args.shell is not None:
                if argv_value:
                    raise ValueError(
                        "Use either --shell or an argument vector, not both"
                    )
                argv_value = ["sh", "-lc", args.shell]
            result = service.execute(
                args.target,
                argv_value,
                timeout=args.timeout,
                max_output_bytes=args.max_output_bytes,
                as_root=args.root,
                workdir=args.workdir,
                env=_environment(args.env),
            )
            _print_data(command, asdict(result), json_mode)
            return result.exit_code

        if command == "enter":
            if json_mode:
                raise ValueError("Interactive enter cannot be used with --json")
            return run_enter(args.target)

        if command == "export":
            added = service.export_packages(args.target)
            _print_data(command, {"added": added}, json_mode)
            return 0

        if command == "list":
            boxes = service.list_boxes()
            _print_data(command, boxes, json_mode)
            return 0

        if command in {"remove", "destroy"}:
            confirmed = args.force
            if json_mode and not confirmed:
                raise PermissionError("JSON destroy requires --force")
            if not confirmed:
                confirmed = _confirm_destroy(args.target)
            if not confirmed:
                if json_mode:
                    print(
                        _json_envelope(
                            command, data={"changed": False, "aborted": True}
                        )
                    )
                else:
                    log("Aborted.")
                return 0
            result = service.destroy(args.target, force=True)
            _print_data(command, asdict(result), json_mode)
            return 0

        if command == "cleanup":
            removed = service.cleanup()
            _print_data(command, {"removed": removed}, json_mode)
            return 0

        parser.error(f"Unknown command: {command}")
        return 2
    except (ValueError, RuntimeError, PermissionError, TimeoutError, OSError) as exc:
        if json_mode:
            print(_json_envelope(command, error=str(exc)))
        else:
            err(str(exc))
        return 1
