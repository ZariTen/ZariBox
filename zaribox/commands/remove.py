from __future__ import annotations

from ..config import load_context
from ..logging import BOLD, RED, RST, err, log, ok, warn
from ..state import StateStore


def run_destroy(container_name: str) -> int:
    try:
        state = StateStore(container_name)
        resolved = state.yaml_path_for(container_name)
        _, config, backend_name, backend = load_context(str(resolved))
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
        state.clear_cache(name)
        ok(f"Container '{name}' destroyed. Home dir preserved.")
        return 0
    except RuntimeError as exc:
        err(str(exc))
        return 1
    except EOFError:
        log("Aborted.")
        return 0
