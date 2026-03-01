from __future__ import annotations

import shutil
import subprocess

from .base import Backend


class DistroboxBackend(Backend):
    name = "distrobox"

    def runtime_present(self) -> bool:
        return shutil.which("distrobox") is not None

    def container_exists(self, name: str) -> bool:
        if not self.runtime_present():
            return False

        result = subprocess.run(
            ["distrobox", "list"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False

        output = result.stdout
        return f"| {name} " in output or any(line.split() and name == line.split()[0] for line in output.splitlines())
