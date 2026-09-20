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
    backend: PodmanBackend


def load_container_context(container_name: str) -> ContainerContext | None:
    try:
        state = StateStore(container_name)
        yaml_path, config, backend = load_context(state.yaml_path_for(container_name))
    except (ValueError, RuntimeError) as exc:
        err(str(exc))
        return None

    return ContainerContext(state, yaml_path, config, backend)


def require_runtime(backend: PodmanBackend) -> bool:
    if backend.runtime_present():
        return True

    err("Podman is not installed or not in PATH.")
    return False
