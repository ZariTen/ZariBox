from __future__ import annotations

from ..backends import make_backend
from ..config import load_config, resolve_backend, resolve_yaml
from ..logging import err, log, warn
from .apply import run_apply


def run_enter(yaml_arg: str | None) -> int:
    try:
        yaml_path = resolve_yaml(yaml_arg)
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
            warn(f"Container '{name}' does not exist. Running apply first...")
            apply_status = run_apply(str(yaml_path))
            if apply_status != 0:
                return apply_status

        log(f"Entering '{name}'...")
        return backend.enter(name)
    except RuntimeError as exc:
        err(str(exc))
        return 1
