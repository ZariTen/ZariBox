from __future__ import annotations

from ..logging import BOLD, RED, RST, err, log, ok, warn
from ._common import load_container_context, require_runtime


def run_destroy(container_name: str) -> int:
    context = load_container_context(container_name)
    if context is None:
        return 1
    if not require_runtime(context.backend_name, context.backend):
        return 1

    name = context.config.name

    try:
        if not context.backend.container_exists(name):
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
            context.backend.stop(name)
        except RuntimeError:
            pass
        context.backend.rm(name)
        context.state.clear_cache(name)
        ok(f"Container '{name}' destroyed. Home dir preserved.")
        return 0
    except RuntimeError as exc:
        err(str(exc))
        return 1
    except EOFError:
        log("Aborted.")
        return 0
