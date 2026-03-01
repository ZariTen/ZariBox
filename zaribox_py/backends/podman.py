from __future__ import annotations

import shutil
import subprocess

from .base import Backend


class PodmanBackend(Backend):
    name = "podman"

    def runtime_present(self) -> bool:
        return shutil.which("podman") is not None

    def container_exists(self, name: str) -> bool:
        if not self.runtime_present():
            return False

        result = subprocess.run(
            ["podman", "container", "inspect", name],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
