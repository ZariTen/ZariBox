from __future__ import annotations

import hashlib
from pathlib import Path

from .models import ZariConfig


class StateStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        root = base_dir or (Path.home() / ".local" / "share" / "zaribox")
        self.cache_dir = root / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def container_hash_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.container.hash"

    def packages_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.packages"

    def saved_container_hash(self, name: str) -> str:
        path = self.container_hash_path(name)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def saved_packages(self, name: str) -> list[str]:
        path = self.packages_path(name)
        if not path.exists():
            return []
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line]


def container_identity_hash(config: ZariConfig) -> str:
    graphics_types = "\n".join(item.type.lower() for item in config.graphics)
    payload = (
        f"{config.image}\n"
        f"{config.home_dir or ''}\n"
        f"{config.extra_flags}\n"
        f"{graphics_types}\n"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def package_drift(desired: list[str], saved: list[str]) -> tuple[list[str], list[str]]:
    desired_set = set(desired)
    saved_set = set(saved)
    to_install = sorted(desired_set - saved_set)
    to_remove = sorted(saved_set - desired_set)
    return to_install, to_remove
