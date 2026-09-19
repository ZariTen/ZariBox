from __future__ import annotations

from ..logging import err, ok
from ..service import ZariBoxService


def run_create(yaml_arg: str | None) -> int:
    """Compatibility adapter for the historical always-recreate command."""
    try:
        result = ZariBoxService().ensure(yaml_arg, force=True, recreate=True)
    except (ValueError, RuntimeError, PermissionError, TimeoutError, OSError) as exc:
        err(str(exc))
        return 1
    ok(f"Container '{result.container}' is ready.")
    return 0
