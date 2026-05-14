from __future__ import annotations

from pathlib import Path

from ..backends import make_backend
from ..config import load_config, resolve_backend, resolve_yaml
from ..logging import err, log, warn
from ..state import StateStore


def run_enter(container_name: str | None) -> int:
    if (
        container_name
        and not container_name.endswith((".yml", ".yaml"))
        and not Path(container_name).exists()
    ):
        state = StateStore(container_name)
        resolved = state.yaml_path_for(container_name)
        if resolved is None:
            err(f"No known container '{container_name}'. Run create first.")
            return 1
        container_name = str(resolved)

    try:
        yaml_path = resolve_yaml(container_name)
        config = load_config(yaml_path)
        backend_name = resolve_backend(config)
        backend = make_backend(backend_name)
    except (ValueError, RuntimeError) as exc:
        err(str(exc))
        return 1

    if not backend.runtime_present():
        err(f"{backend_name} backend is not installed or not in PATH.")
        return 1

    name = config.name

    try:
        if not backend.container_exists(name):
            warn(f"Container '{name}' does not exist.")

        log(f"Entering '{name}'...")
        return backend.enter(name)
    except RuntimeError as exc:
        err(str(exc))
        return 1
