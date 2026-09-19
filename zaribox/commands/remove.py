from __future__ import annotations

from ..logging import err, log, ok
from ..service import ZariBoxService


def run_destroy(container_name: str, *, force: bool = False) -> int:
    if not force:
        try:
            confirmed = (
                input(
                    f"This will destroy container '{container_name}' "
                    "(home directory is preserved). Confirm? [y/N] "
                )
                .strip()
                .lower()
            )
        except EOFError:
            confirmed = ""
        if confirmed != "y":
            log("Aborted.")
            return 0
    try:
        result = ZariBoxService().destroy(container_name, force=True)
    except (ValueError, RuntimeError, PermissionError, TimeoutError, OSError) as exc:
        err(str(exc))
        return 1
    if result.changed:
        ok(f"Container '{result.container}' destroyed. Home directory preserved.")
    else:
        log(f"Container '{result.container}' did not exist; stale state was cleared.")
    return 0
