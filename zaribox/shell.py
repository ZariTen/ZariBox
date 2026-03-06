from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Sequence


@dataclass(slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    def __init__(self, args: Sequence[str], returncode: int, stderr: str) -> None:
        command_text = " ".join(args)
        message = f"Command failed ({returncode}): {command_text}"
        stderr_text = stderr.strip()
        if stderr_text:
            message = f"{message}\n{stderr_text}"
        super().__init__(message)
        self.args_list = list(args)
        self.returncode = returncode
        self.stderr = stderr


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

    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""

    if check and completed.returncode != 0:
        raise CommandError(args, completed.returncode, stderr)

    return CommandResult(
        args=list(args),
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
    )
