from __future__ import annotations

import os
import re
from pathlib import Path

from .models import ZariConfig


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
    return candidates[0]


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return _load_yaml_fallback(path)

    with path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"Top-level YAML document must be a mapping: {path}")
    return raw


def _strip_comments(line: str) -> str:
    if "#" not in line:
        return line.rstrip()
    return line.split("#", 1)[0].rstrip()


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and (
        (text[0] == '"' and text[-1] == '"') or (text[0] == "'" and text[-1] == "'")
    ):
        return text[1:-1]
    return text


def _load_yaml_fallback(path: Path) -> dict:
    data: dict[str, object] = {}
    current_list_key: str | None = None

    key_line_pattern = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = _strip_comments(raw_line)
        if not line.strip():
            continue

        stripped = line.strip()

        key_match = key_line_pattern.match(stripped)
        if key_match:
            key, value = key_match.group(1), key_match.group(2).strip()
            if value:
                data[key] = _unquote(value)
                current_list_key = None
            else:
                if key not in data or not isinstance(data.get(key), list):
                    data[key] = []
                current_list_key = key
            continue

        if current_list_key and stripped.startswith("-"):
            item = stripped[1:].strip()
            target = data.get(current_list_key)
            if not isinstance(target, list):
                target = []
                data[current_list_key] = target

            if not item:
                continue

            inline_kv = key_line_pattern.match(item)
            if inline_kv:
                target.append({inline_kv.group(1): _unquote(inline_kv.group(2))})
            else:
                target.append(_unquote(item))

    return data


def _normalize_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def load_config(path: Path) -> ZariConfig:
    raw = _load_yaml(path)

    image = str(raw.get("Image", "")).strip()
    if not image:
        raise ValueError(f"Image field is required in {path}")

    name = str(raw.get("Name", "")).strip() or path.stem
    backend = raw.get("Backend")
    backend_name = str(backend).strip() if backend is not None else None

    home_dir_value = raw.get("HomeDir")
    home_dir = str(home_dir_value).strip() if home_dir_value is not None else None

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
