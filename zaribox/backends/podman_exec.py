from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence

from .podman_args import env_args


def build_exec_args(
    name: str,
    command: Sequence[str],
    *,
    user_args: Sequence[str],
    workdir: str | None = None,
    env: Mapping[str, str] | None = None,
    graphics_env: Sequence[str] = (),
    interactive: bool = False,
) -> list[str]:
    args = ["podman", "exec"]
    if interactive:
        args.append("-it")
    args.extend(user_args)
    if workdir:
        if not workdir.startswith("/"):
            raise ValueError("Container workdir must be an absolute path")
        args.extend(["--workdir", workdir])
    args.extend(env_args(env or {}))
    args.extend([*graphics_env, name, *command])
    return args


def login_shell_command(preferred_shell: str) -> str:
    shell = shlex.quote(preferred_shell)
    return (
        f"if command -v {shell} >/dev/null 2>&1; then exec {shell} -l; "
        f"elif command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh -l; fi"
    )
