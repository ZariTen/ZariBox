from __future__ import annotations

import shlex
import subprocess
from typing import Sequence

from .base import Backend
from ..shell import CommandError, CommandResult, command_exists, run_command


class DistroboxBackend(Backend):
    name = "distrobox"

    def runtime_present(self) -> bool:
        return command_exists("distrobox")

    def _raise_on_failure(self, result: CommandResult) -> None:
        if result.returncode != 0:
            raise RuntimeError(
                f"distrobox backend command failed\n{result.stderr.strip()}" if result.stderr.strip() else "distrobox backend command failed"
            )

    def container_exists(self, name: str) -> bool:
        if not self.runtime_present():
            return False

        result = run_command(["distrobox", "list"], capture_output=True)
        if result.returncode != 0:
            return False

        output = result.stdout
        return f"| {name} " in output or any(line.split() and name == line.split()[0] for line in output.splitlines())

    def create(
        self,
        name: str,
        image: str,
        home_dir: str,
        extra_flags: str = "",
        graphics_types: Sequence[str] | None = None,
    ) -> None:
        del graphics_types
        args = ["distrobox", "create", "--name", name, "--image", image, "--home", home_dir]
        if extra_flags.strip():
            args.extend(shlex.split(extra_flags))

        try:
            result = run_command(args, capture_output=True)
        except CommandError as exc:
            raise RuntimeError(str(exc)) from exc
        self._raise_on_failure(result)

    def exec(
        self,
        name: str,
        command: Sequence[str],
        *,
        as_user: bool = False,
        check: bool = True,
        capture_output: bool = True,
    ) -> CommandResult:
        del as_user
        args = ["distrobox", "enter", name, "--", *command]
        result = run_command(args, capture_output=capture_output)
        if check and result.returncode != 0:
            raise RuntimeError(
                f"distrobox exec failed\n{result.stderr.strip()}" if result.stderr.strip() else "distrobox exec failed"
            )
        return result

    def enter(self, name: str) -> int:
        result = subprocess.run(["distrobox", "enter", name], check=False)
        return result.returncode

    def stop(self, name: str) -> None:
        result = run_command(["distrobox", "stop", name, "--yes"], capture_output=True)
        self._raise_on_failure(result)

    def rm(self, name: str) -> None:
        result = run_command(["distrobox", "rm", name, "--force"], capture_output=True)
        self._raise_on_failure(result)

    def ps(self) -> str:
        result = run_command(["distrobox", "list"], capture_output=True)
        self._raise_on_failure(result)
        return result.stdout
