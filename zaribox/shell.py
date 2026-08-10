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
    completed = subprocess.run(
        list(args),
        check=False,
        capture_output=capture_output,
        text=True,
    )

    stdout = completed.stdout
    stderr = completed.stderr

    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, list(args), output=stdout, stderr=stderr
        )

    return CommandResult(
        args=list(args),
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
    )
