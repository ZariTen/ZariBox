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

    def save_container_hash(self, name: str, value: str) -> None:
        self.container_hash_path(name).write_text(value, encoding="utf-8")

    def saved_packages(self, name: str) -> list[str]:
        path = self.packages_path(name)
        if not path.exists():
            return []
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line]

    def save_packages(self, name: str, packages: list[str]) -> None:
        path = self.packages_path(name)
        if not packages:
            path.write_text("", encoding="utf-8")
            return

        package_lines = sorted(
            {package.strip() for package in packages if package.strip()}
        )
        path.write_text("\n".join(package_lines) + "\n", encoding="utf-8")

    def clear_cache(self, name: str) -> None:
        for path in (self.container_hash_path(name), self.packages_path(name)):
            try:
                path.unlink()
            except FileNotFoundError:
                continue


def container_identity_hash(config: ZariConfig) -> str:
    payload = f"{config.image}\n{config.home_dir or ''}\n{config.extra_flags}\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def package_drift(desired: list[str], saved: list[str]) -> tuple[list[str], list[str]]:
    desired_set = set(desired)
    saved_set = set(saved)
    to_install = sorted(desired_set - saved_set)
    to_remove = sorted(saved_set - desired_set)
    return to_install, to_remove
