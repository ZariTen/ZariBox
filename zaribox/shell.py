from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def command_exists(binary: str) -> bool:
    return shutil.which(binary) is not None


def run_command(
    args: Sequence[str],
    *,
    check: bool = False,
    capture_output: bool = True,
) -> CommandResult:
    command = list(args)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=capture_output,
        text=True,
    )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""

    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, command, output=stdout, stderr=stderr
        )

    return CommandResult(
        args=command,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
    )
