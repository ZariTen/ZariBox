from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import yaml

from .models import ZariConfig

def _resolve_image(image: str) -> str:
    first = image.split("/")[0]
    if "." not in first and ":" not in first:
        image = f"docker.io/library/{image}" if "/" not in image else f"docker.io/{image}"
    if ":" not in image.split("/")[-1]:
        image = f"{image}:latest"
    return image

def resolve_yaml(arg: str | None) -> Path:
    if arg:
        direct = Path(arg)
        if direct.is_file():
            return direct

        candidate_yaml = Path(f"{arg}.yaml")
        if candidate_yaml.is_file():
            return candidate_yaml

        candidate_yml = Path(f"{arg}.yml")
        if candidate_yml.is_file():
            return candidate_yml

    candidates = sorted(Path.cwd().glob("*.yaml")) + sorted(Path.cwd().glob("*.yml"))
    if not candidates:
        raise ValueError(
            "No .yaml file found. Pass one explicitly or run from a directory containing one."
        )
    if len(candidates) > 1:
        choices = ", ".join(path.name for path in candidates)
        raise ValueError(
            "Multiple YAML files found. Pass one explicitly (for example: "
            f"'zaribox status {candidates[0].name}'). Found: {choices}"
        )
    return candidates[0]


def _load_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as stream:
        loaded_obj: object = yaml.safe_load(stream)

    if loaded_obj is None:
        loaded: dict[object, object] = {}
    elif isinstance(loaded_obj, dict):
        loaded = cast(dict[object, object], loaded_obj)
    else:
        raise ValueError(f"Top-level YAML document must be a mapping: {path}")

    return {str(key): value for key, value in loaded.items()}


def _normalize_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = cast(list[object], value)
        return [str(item).strip() for item in items if str(item).strip()]
    return []


def load_config(path: Path) -> ZariConfig:
    raw = _load_yaml(path)

    image = str(raw.get("Image", "")).strip()
    if not image:
        raise ValueError(f"Image field is required in {path}")
    image = _resolve_image(image)

    name = str(raw.get("Name", "")).strip() or path.stem
    backend = raw.get("Backend")
    backend_name = str(backend).strip() if backend is not None else None

    home_dir_value = raw.get("HomeDir")
    home_dir = os.path.expandvars(str(home_dir_value).strip()) if home_dir_value is not None else None

    extra_flags_value = raw.get("ExtraFlags")
    extra_flags = (
        str(extra_flags_value).strip() if extra_flags_value is not None else ""
    )

    return ZariConfig(
        file_path=path,
        name=name,
        image=image,
        backend=backend_name or None,
        home_dir=home_dir or None,
        extra_flags=extra_flags,
        packages=_normalize_list(raw.get("Packages")),
        run=_normalize_list(raw.get("Run")),
    )


def resolve_backend(config: ZariConfig | None) -> str:
    selected = os.environ.get("ZARIBOX_BACKEND", "").strip()
    if not selected and config is not None and config.backend:
        selected = config.backend.strip()
    if not selected:
        selected = "distrobox"

    if selected not in {"distrobox", "podman"}:
        raise ValueError(
            f"Unsupported backend: '{selected}'. Supported: distrobox, podman"
        )
    return selected
