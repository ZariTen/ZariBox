from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .models import ZariConfig


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


class StateStore:
    def __init__(self, container_name: str = "") -> None:
        self.cache_dir = (
            _config_dir() / "zaribox" / container_name
            if container_name != ""
            else _config_dir() / "zaribox"
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def container_hash_path(self, container_name: str) -> Path:
        return self.cache_dir / f"{container_name}.hash"

    def packages_path(self, container_name: str) -> Path:
        return self.cache_dir / f"{container_name}.packages"

    def yaml_path_cache_path(self, container_name: str) -> Path:
        return self.cache_dir / f"{container_name}.yaml_path"

    def saved_container_hash(self, container_name: str) -> str:
        path = self.container_hash_path(container_name)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def save_container_hash(self, container_name: str, value: str) -> None:
        self.container_hash_path(container_name).write_text(value, encoding="utf-8")

    def saved_packages(self, container_name: str) -> list[str]:
        path = self.packages_path(container_name)
        if not path.exists():
            return []
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line]

    def save_packages(self, container_name: str, packages: list[str]) -> None:
        path = self.packages_path(container_name)
        if not packages:
            path.write_text("", encoding="utf-8")
            return

        package_lines = sorted(
            {package.strip() for package in packages if package.strip()}
        )
        path.write_text("\n".join(package_lines) + "\n", encoding="utf-8")

    def clear_cache(self, container_name: str) -> None:
        for path in (
            self.container_hash_path(container_name),
            self.packages_path(container_name),
            self.yaml_path_cache_path(container_name),
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                continue

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
        self.yaml_path_cache_path(container_name).write_text(stored, encoding="utf-8")


def _normalize_image(image: str) -> str:
    image = image.strip()
    for prefix in ("docker.io/library/", "docker.io/"):
        if image.startswith(prefix):
            image = image[len(prefix) :]
    if ":" not in image:
        image += ":latest"
    return image


def container_identity_hash(config: ZariConfig) -> str:
    payload = f"{_normalize_image(config.image)}\n{config.home_dir or ''}\n{config.extra_flags}\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def package_drift(desired: list[str], saved: list[str]) -> tuple[list[str], list[str]]:
    desired_set = set(desired)
    saved_set = set(saved)
    to_install = sorted(desired_set - saved_set)
    to_remove = sorted(saved_set - desired_set)
    return to_install, to_remove
