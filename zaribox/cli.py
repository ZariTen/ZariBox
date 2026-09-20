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
        usage="%(prog)s [OPTIONS] COMMAND ...",
        description="Create and manage reproducible Podman development containers.",
        epilog="""common workflows:
  zaribox create archbox.yaml     Create or update a box
  zaribox enter archbox           Open an interactive shell
  zaribox status archbox          Check its current state
  zaribox exec archbox -- git status

A TARGET can be a container name or its manifest path.
Run 'zaribox COMMAND --help' for full command details.""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        json_errors=json_errors,
    )
    parser.add_argument(
        "--version", action="version", version=f"ZariBox v{__version__}"
    )
    parser.add_argument(
        "--json", action="store_true", help="print one machine-readable JSON document"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="disable colored output"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="COMMAND",
    )

    def command(
        name: str, summary: str, description: str, example: str
    ) -> argparse.ArgumentParser:
        return subparsers.add_parser(
            name,
            help=summary,
            description=description,
            epilog=f"example:\n  {example}",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

    validate = command(
        "validate",
        "Check a manifest without changing anything",
        "Parse a manifest and verify its fields, backend, mounts, and security "
        "policy.\n"
        "Reports the resolved name, image, kind, and security profile.",
        "zaribox validate archbox.yaml",
    )
    validate.add_argument(
        "config",
        nargs="?",
        metavar="MANIFEST",
        help="YAML manifest; auto-detected in the current directory when omitted",
    )

    plan = command(
        "plan",
        "Preview what create would change",
        "Compare the requested configuration with the managed container and list "
        "every\n"
        "planned action. Reports whether destructive work requires --force.",
        "zaribox plan archbox",
    )
    plan.add_argument(
        "target",
        nargs="?",
        metavar="TARGET",
        help="container name or manifest; auto-detected when omitted",
    )

    create = command(
        "create",
        "Create a box or bring it up to date",
        "Create a missing container or reconcile an existing one with its manifest.\n"
        "The dedicated home directory survives updates and explicit recreation.",
        "zaribox create archbox.yaml",
    )
    create.add_argument(
        "target",
        nargs="?",
        metavar="TARGET",
        help="container name or manifest; auto-detected when omitted",
    )
    create.add_argument(
        "--force",
        action="store_true",
        help="allow package removal or other destructive changes",
    )
    create.add_argument(
        "--recreate",
        action="store_true",
        help="rebuild the container even when already in sync",
    )
    create.add_argument(
        "--lock-timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="maximum time to wait for another operation (default: 30)",
    )

    status = command(
        "status",
        "Show configuration and runtime state",
        "Report whether the container exists and whether its image, security policy,\n"
        "configuration, and package snapshot match the manifest.",
        "zaribox status archbox",
    )
    status.add_argument("target", metavar="TARGET", help="container name or manifest")

    execute = command(
        "exec",
        "Run a non-interactive command inside a box",
        "Execute a bounded command in a managed container and capture its exit code,\n"
        "stdout, and stderr. Place -- before the command and its arguments.",
        "zaribox exec archbox -- git status",
    )
    execute.add_argument("target", metavar="TARGET", help="container name or manifest")
    execute.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        metavar="SECONDS",
        help="terminate the command after this duration (default: 300)",
    )
    execute.add_argument(
        "--max-output-bytes",
        type=int,
        default=1_048_576,
        metavar="BYTES",
        help="truncate captured output beyond this size (default: 1048576)",
    )
    execute.add_argument(
        "--workdir", metavar="PATH", help="working directory inside the container"
    )
    execute.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="set an environment value; may be repeated",
    )
    execute.add_argument(
        "--root",
        action="store_true",
        help="run as root instead of the container user",
    )
    execute.add_argument(
        "--shell", metavar="COMMAND", help="run an explicit command through sh -lc"
    )
    execute.add_argument(
        "argv", nargs="*", metavar="COMMAND", help="command and arguments after --"
    )

    enter = command(
        "enter",
        "Open an interactive shell inside a box",
        "Start the container when needed and attach an interactive shell as the\n"
        "container user. Disabled for restricted AgentBoxes; use exec instead.",
        "zaribox enter archbox",
    )
    enter.add_argument("target", metavar="NAME", help="managed desktop container name")

    export = command(
        "export",
        "Save manually installed packages to the manifest",
        "Read the distribution's explicitly installed packages and atomically add new\n"
        "entries to the manifest. Dependency-only packages are ignored.",
        "zaribox export archbox",
    )
    export.add_argument("target", metavar="TARGET", help="container name or manifest")

    command(
        "list",
        "List managed boxes and their runtime state",
        "List containers recorded in ZariBox project state, including whether each\n"
        "container exists and is currently running.",
        "zaribox list",
    )

    remove = command(
        "remove",
        "Remove a box while preserving its home",
        "Stop and remove a managed container and clear its ZariBox state. The "
        "dedicated\n"
        "home directory is not deleted, so it can be reused by a later create.",
        "zaribox remove archbox",
    )
    remove.add_argument("target", metavar="TARGET", help="container name or manifest")
    remove.add_argument(
        "--force", action="store_true", help="skip the interactive confirmation prompt"
    )

    command(
        "cleanup",
        "Remove expired AgentBoxes and stale leases",
        "Remove AgentBoxes whose TTL has expired and clear abandoned operation "
        "leases.\n"
        "Active boxes and persistent home directories are preserved.",
        "zaribox cleanup",
    )
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

        if command == "create":
            target = args.target
            config_path: str | Path | None = target
            if target is not None and not Path(target).expanduser().is_file():
                config_path = service.config_for_target(target).file_path
            result = service.ensure(
                config_path,
                force=bool(args.force) or bool(args.recreate),
                recreate=bool(args.recreate),
                lock_timeout=args.lock_timeout,
            )
            _print_data(command, asdict(result), json_mode)
            return 0

        if command == "status":
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

        if command == "remove":
            confirmed = args.force
            if json_mode and not confirmed:
                raise PermissionError("JSON remove requires --force")
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
