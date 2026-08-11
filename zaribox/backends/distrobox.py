from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence

from ..shell import CommandResult, command_exists, run_command
from .base import Backend


class DistroboxBackend(Backend):
    name = "distrobox"

    def __init__(self) -> None:
        self._container_names: set[str] | None = None

    def runtime_present(self) -> bool:
        return command_exists("distrobox")

    def _raise_on_failure(self, result: CommandResult, context: str) -> None:
        if result.returncode != 0:
            stderr_text = result.stderr.strip()
            message = f"{context} failed"
            if stderr_text:
                message = f"{message}\n{stderr_text}"
            raise RuntimeError(message)

    def _list_names(self) -> set[str]:
        """Return the names of all existing containers, fetched once per instance."""
        if self._container_names is None:
            names: set[str] = set()
            if self.runtime_present():
                result = run_command(["distrobox", "list"], capture_output=True)
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        if "|" in line:
                            cells = [cell.strip() for cell in line.split("|")]
                            name = cells[1] if len(cells) > 1 else ""
                            if name and name != "NAME":  # skip table header
                                names.add(name)
                        else:
                            names.add(line.split()[0])
            self._container_names = names
        return self._container_names

    def container_exists(self, name: str) -> bool:
        return name in self._list_names()

    def create(
        self,
        name: str,
        image: str,
        home_dir: str,
        extra_flags: str = "",
    ) -> None:
        args = [
            "distrobox",
            "create",
            "--name",
            name,
            "--image",
            image,
            "--home",
            home_dir,
            "--yes",
        ]
        if extra_flags.strip():
            args.extend(shlex.split(extra_flags))

        result = run_command(args, capture_output=False)
        self._raise_on_failure(result, "distrobox create")

    def exec(
        self,
        name: str,
        command: Sequence[str],
        *,
        as_user: bool = False,
        check: bool = True,
        capture_output: bool = True,
    ) -> CommandResult:
        args = ["distrobox", "enter", name, "--", *command]
        if not as_user:
            args.insert(2, "--root")

        result = run_command(args, capture_output=capture_output)
        if check:
            self._raise_on_failure(result, "distrobox exec")
        return result

    def post_install(self, name: str, home_dir: str) -> None:
        pass

    def enter(self, name: str) -> int:
        result = subprocess.run(["distrobox", "enter", name], check=False)
        return result.returncode

    def stop(self, name: str) -> None:
        result = run_command(["distrobox", "stop", name, "--yes"], capture_output=True)
        self._raise_on_failure(result, "distrobox stop")

    def rm(self, name: str) -> None:
        result = run_command(["distrobox", "rm", name, "--force"], capture_output=True)
        self._raise_on_failure(result, "distrobox rm")

    def ps(self) -> str:
        result = run_command(["distrobox", "list"], capture_output=True)
        self._raise_on_failure(result, "distrobox list")
        return result.stdout
