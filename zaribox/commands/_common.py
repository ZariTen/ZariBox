from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..backends import PodmanBackend
from ..config import load_context
from ..logging import err
from ..models import ZariConfig
from ..state import StateStore


@dataclass(frozen=True, slots=True)
class ContainerContext:
    state: StateStore
    yaml_path: Path
    config: ZariConfig
    backend_name: str
    backend: PodmanBackend


def load_container_context(container_name: str) -> ContainerContext | None:
    try:
        state = StateStore(container_name)
        yaml_path, config, backend_name, backend = load_context(
            state.yaml_path_for(container_name)
        )
    except (ValueError, RuntimeError) as exc:
        err(str(exc))
        return None

    return ContainerContext(state, yaml_path, config, backend_name, backend)


def require_runtime(backend_name: str, backend: PodmanBackend) -> bool:
    if backend.runtime_present():
        return True

    err(f"{backend_name} backend is not installed or not in PATH.")
    return False
