from __future__ import annotations

from ..backends import PodmanBackend
from ..logging import err, ok
from ..models import ZariConfig
from ..service import ZariBoxService


def _sync_from_config(config: ZariConfig, backend: PodmanBackend) -> int:
    """Compatibility adapter used by older callers and extensions."""
    try:
        ZariBoxService(backend).ensure(config.file_path, force=True)
    except (ValueError, RuntimeError, PermissionError, TimeoutError, OSError) as exc:
        err(str(exc))
        return 1
    ok("Container is in sync.")
    return 0


def run_sync(container_name: str) -> int:
    service = ZariBoxService()
    try:
        config = service.config_for_target(container_name)
        service.ensure(config.file_path, force=True)
    except (ValueError, RuntimeError, PermissionError, TimeoutError, OSError) as exc:
        err(str(exc))
        return 1
    ok("Container is in sync.")
    return 0
