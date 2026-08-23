from __future__ import annotations

from ..config import load_context
from ..logging import err, log, warn
from ..state import StateStore


def run_enter(container_name: str) -> int:
    try:
        state = StateStore(container_name)
        resolved = state.yaml_path_for(container_name)
        _, config, backend_name, backend = load_context(resolved)
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
