from __future__ import annotations

from ..backends import make_backend
from ..config import load_config, resolve_backend, resolve_yaml
from ..logging import BOLD, RED, RST, err, log, ok, warn
from ..state import StateStore


def run_destroy(container_name: str) -> int:
    try:
        state = StateStore(container_name)
        resolved = state.yaml_path_for(container_name)
        if resolved is None:
            err(f"No known container '{container_name}'.")
            return 1
        yaml_path = resolve_yaml(str(resolved))
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
            return 0

        print(
            f"{RED}{BOLD}This will destroy container '{name}' (home dir is preserved).{RST}"
        )
        confirm = input("  Confirm? [y/N] ").strip()
        if confirm not in {"y", "Y"}:
            log("Aborted.")
            return 0

        try:
            backend.stop(name)
        except RuntimeError:
            pass
        backend.rm(name)
        StateStore(container_name).clear_cache(name)
        ok(f"Container '{name}' destroyed. Home dir preserved.")
        return 0
    except RuntimeError as exc:
        err(str(exc))
        return 1
    except EOFError:
        log("Aborted.")
        return 0
