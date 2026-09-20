from __future__ import annotations

from ..logging import err, log, warn
from ._common import load_container_context, require_runtime


def run_enter(container_name: str) -> int:
    context = load_container_context(container_name)
    if context is None:
        return 1
    if not require_runtime(context.backend):
        return 1

    name = context.config.name
    if context.config.kind == "AgentBox" or context.config.profile in {
        "agent",
        "restricted",
    }:
        err(
            "Interactive enter is disabled for agent-profile containers; use 'zaribox exec'."
        )
        return 1
    try:
        if not context.backend.container_exists(name):
            warn(f"Container '{name}' does not exist.")
        log(f"Entering '{name}'...")
        return context.backend.enter(name)
    except RuntimeError as exc:
        err(str(exc))
        return 1
