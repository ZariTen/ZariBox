from ..backends import PodmanBackend
from ..config import load_context
from ..logging import err, ok, step
from ..models import ZariConfig
from ..pkgmgr import detect_pkgmgr, install_cmd, remove_cmd
from ..state import StateStore, package_drift


def _run_package_install(
    backend: PodmanBackend, name: str, packages: list[str], image: str
) -> None:
    mgr = detect_pkgmgr(image)
    cmd = install_cmd(mgr)
    step(f"Installing {len(packages)} package(s) via {mgr}...")
    _ = backend.exec(name, ["bash", "-c", cmd, "_", *packages], as_user=False)
    ok(f"Packages installed: {' '.join(packages)}")


def _run_package_remove(
    backend: PodmanBackend, name: str, packages: list[str], image: str
) -> None:
    mgr = detect_pkgmgr(image)
    cmd = remove_cmd(mgr)
    step(f"Removing {len(packages)} package(s): {' '.join(packages)}")
    _ = backend.exec(name, ["bash", "-c", cmd, "_", *packages], as_user=False)
    ok(f"Removed: {' '.join(packages)}")


def _sync_from_config(
    config: ZariConfig, backend: PodmanBackend, backend_name: str
) -> int:
    if not backend.runtime_present():
        err(f"{backend_name} is not installed or not in PATH.")
        return 1

    name = config.name
    state = StateStore(name)

    if not backend.container_exists(name):
        err(f"Container '{name}' does not exist. Run 'zaribox create' first.")
        return 1

    desired = config.packages
    saved = state.saved_packages(name)
    to_install, to_remove = package_drift(desired, saved)

    if not to_install and not to_remove:
        ok("Packages already in sync — nothing to do.")
        return 0

    try:
        if to_install:
            _run_package_install(backend, name, to_install, config.image)
        if to_remove:
            _run_package_remove(backend, name, to_remove, config.image)
    except RuntimeError as exc:
        err(str(exc))
        return 1

    state.save_packages(name, desired)
    ok("Packages synced.")
    return 0


def run_sync(container_name: str) -> int:
    try:
        state = StateStore(container_name)
        resolved = state.yaml_path_for(container_name)
        _yaml_path, config, backend_name, backend = load_context(resolved)
    except (ValueError, RuntimeError) as exc:
        err(str(exc))
        return 1

    return _sync_from_config(config, backend, backend_name)
