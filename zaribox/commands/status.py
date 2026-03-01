from __future__ import annotations

from ..backends import make_backend
from ..config import load_config, resolve_backend, resolve_yaml
from ..logging import CYN, DIM, GRN, RED, RST, YLW, err
from ..state import StateStore, container_identity_hash, package_drift


def run_status(yaml_arg: str | None) -> int:
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

    state = StateStore()
    current_hash = container_identity_hash(config)
    saved_hash = state.saved_container_hash(config.name)
    desired_packages = config.packages
    saved_packages = state.saved_packages(config.name)
    to_install, to_remove = package_drift(desired_packages, saved_packages)

    print()
    print(f"{CYN}ZariBox Status{RST}")
    print(f"  config   : {yaml_path}")
    print(f"  name     : {config.name}")
    print(f"  image    : {config.image}")

    exists = backend.container_exists(config.name)
    print(f"  exists   : {GRN}yes{RST}" if exists else f"  exists   : {RED}no{RST}")

    if current_hash == saved_hash:
        print(f"  container: {GRN}in sync{RST}")
    else:
        print(f"  container: {YLW}changed -- will recreate on apply{RST}")

    if not to_install and not to_remove:
        print(f"  packages : {GRN}in sync{RST}")
    else:
        print(f"  packages : {YLW}drift detected{RST}")
        for package_name in to_install:
            print(f"    {GRN}+ {package_name}{RST}  {DIM}(to install){RST}")
        for package_name in to_remove:
            print(f"    {RED}- {package_name}{RST}  {DIM}(to remove){RST}")

    print("  packages :")
    for package_name in desired_packages:
        print(f"    {DIM}-{RST} {package_name}")
    print()
    return 0
