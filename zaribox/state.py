from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .models import ZariConfig
from .project_state import atomic_write


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


class StateStore:
    def __init__(self, container_name: str = "") -> None:
        self.cache_dir: Path = (
            _config_dir() / "zaribox" / container_name
            if container_name != ""
            else _config_dir() / "zaribox"
        )

    def _cache_path(self, container_name: str, suffix: str) -> Path:
        return self.cache_dir / f"{container_name}{suffix}"

    def container_hash_path(self, container_name: str) -> Path:
        return self._cache_path(container_name, ".hash")

    def packages_path(self, container_name: str) -> Path:
        return self._cache_path(container_name, ".packages")

    def yaml_path_cache_path(self, container_name: str) -> Path:
        return self._cache_path(container_name, ".yaml_path")

    def saved_container_hash(self, container_name: str) -> str:
        path = self.container_hash_path(container_name)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def save_container_hash(self, container_name: str, value: str) -> None:
        atomic_write(self.container_hash_path(container_name), value)

    def saved_packages(self, container_name: str) -> list[str]:
        path = self.packages_path(container_name)
        if not path.exists():
            return []
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line]

    def save_packages(self, container_name: str, packages: list[str]) -> None:
        path = self.packages_path(container_name)
        if not packages:
            atomic_write(path, "")
            return

        package_lines = sorted(
            {package.strip() for package in packages if package.strip()}
        )
        atomic_write(path, "\n".join(package_lines) + "\n")

    def clear_cache(self, container_name: str) -> None:
        for path in (
            self.container_hash_path(container_name),
            self.packages_path(container_name),
            self.yaml_path_cache_path(container_name),
        ):
            path.unlink(missing_ok=True)

        try:
            self.cache_dir.rmdir()
        except OSError:
            pass

    def yaml_path_for(self, container_name: str) -> Path | None:
        path = self.yaml_path_cache_path(container_name)
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8").strip()
        return Path(raw).expanduser()

    def save_yaml_path(self, container_name: str, yaml_path: Path) -> None:
        try:
            relative = yaml_path.relative_to(Path.home())
            stored = f"~/{relative}"
        except ValueError:
            stored = str(yaml_path)
        atomic_write(self.yaml_path_cache_path(container_name), stored)


def _normalize_image(image: str) -> str:
    image = image.strip()
    for prefix in ("docker.io/library/", "docker.io/"):
        image = image.removeprefix(prefix)
    if ":" not in image:
        image += ":latest"
    return image


def container_identity_hash(config: ZariConfig) -> str:
    requested_profile = (config.profile or "").lower()
    agent_profile = config.kind == "AgentBox" or requested_profile in {
        "agent",
        "restricted",
    }
    effective_profile = "agent" if agent_profile else "default"
    effective_network = config.network or ("none" if agent_profile else "host")
    payload = {
        "name": config.name,
        "image": _normalize_image(config.image),
        "backend": config.backend or "podman",
        "home_dir": config.home_dir or "",
        "home_mount": config.home_mount,
        "extra_flags": config.extra_flags,
        "mounts": [
            {
                "source": mount.source,
                "target": mount.target,
                "read_only": mount.read_only,
                "options": list(mount.options),
            }
            for mount in config.mounts
        ],
        "env": dict(sorted(config.env.items())),
        "workdir": config.workdir,
        "run": list(config.run),
        "network": effective_network,
        "profile": effective_profile,
        "ipc": "private" if agent_profile else "host",
        "graphics": not agent_profile,
        "resources": {
            "cpus": config.resources.cpus
            if config.resources.cpus is not None
            else (2 if agent_profile else None),
            "memory": config.resources.memory
            if config.resources.memory is not None
            else ("2g" if agent_profile else None),
            "pids_limit": config.resources.pids_limit
            if config.resources.pids_limit is not None
            else (256 if agent_profile else None),
        },
        "read_only_root": config.security.read_only_root_filesystem,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def package_drift(desired: list[str], saved: list[str]) -> tuple[list[str], list[str]]:
    desired_set = set(desired)
    saved_set = set(saved)
    to_install = sorted(desired_set - saved_set)
    to_remove = sorted(saved_set - desired_set)
    return to_install, to_remove
