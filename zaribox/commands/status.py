from __future__ import annotations

from ..config import load_context
from ..logging import CYN, DIM, GRN, RED, RST, YLW, err
from ..state import StateStore, container_identity_hash, package_drift
from .export import _fetch_installed_packages


def run_status(container_name: str | None) -> int:
    if not container_name:
        err("Container name is required.")
        return 1

    try:
        state = StateStore(container_name)
        resolved = state.yaml_path_for(container_name)
        yaml_path, config, backend_name, backend = load_context(resolved)
    except (ValueError, RuntimeError) as exc:
        err(str(exc))
        return 1

    if not backend.runtime_present():
        err(f"{backend_name} backend is not installed or not found in PATH.")
        return 1

    current_hash = container_identity_hash(config)
    saved_hash = state.saved_container_hash(config.name)

    desired_packages = config.packages
    saved_packages = state.saved_packages(config.name)
    export_packages = _fetch_installed_packages(backend, config.name, config.image)

    to_install, to_remove = package_drift(desired_packages, saved_packages)
    to_export, _ = package_drift(desired_packages + export_packages, saved_packages)

    print(f"\n{CYN}ZARIBOX STATUS{RST}")
    print(f"  {DIM}Config Path:{RST}  {yaml_path}")
    print(f"  {DIM}Container:{RST}    {config.name}")
    print(f"  {DIM}Base Image:{RST}   {config.image}")

    exists = backend.container_exists(config.name)
    env_status = f"{GRN}Active{RST}" if exists else f"{RED}Not Created{RST}"
    print(f"  {DIM}Environment:{RST}  {env_status}")

    sync_status = (
        f"{GRN}In Sync{RST}" if current_hash == saved_hash else f"{YLW}Modified{RST}"
    )
    print(f"  {DIM}Config Sync:{RST}  {sync_status}")

    print(f"\n{CYN}PACKAGE DRIFT ANALYSIS{RST}")

    if to_export:
        print(f"  {YLW}Runtime Drift Detected ({len(to_export)}){RST}")
        for pkg in to_export:
            print(f"    - {pkg:<22} {DIM}(untracked in container; needs export){RST}")
    else:
        print(
            f"  {GRN}Runtime Sync:{RST} Live container matches configuration baseline."
        )

    if to_install or to_remove:
        print(f"\n  {YLW}Declarative Drift Detected{RST}")
        for pkg in to_install:
            print(f"    + {pkg:<22} {DIM}(missing from container; requires sync){RST}")
        for pkg in to_remove:
            print(f"    - {pkg:<22} {DIM}(removed from config; requires sync){RST}")
    else:
        print(f"  {GRN}Local Sync:{RST} Local config matches snapshot state.")

    print(f"\n{CYN}TARGET CONFIGURATION ({len(desired_packages)} packages){RST}")
    if desired_packages:
        for pkg in desired_packages:
            print(f"  - {pkg}")
    else:
        print(f"    {DIM}(No target packages defined){RST}")
    print()

    return 0
