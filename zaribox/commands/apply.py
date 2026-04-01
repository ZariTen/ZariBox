from __future__ import annotations

from pathlib import Path

from ..backends import Backend, make_backend
from ..config import load_config, resolve_backend, resolve_yaml
from ..logging import BOLD, CYN, DIM, RST, err, log, ok, step, warn
from ..pkgmgr import detect_pkgmgr, install_cmd, remove_cmd
from ..state import StateStore, container_identity_hash, package_drift


def _default_home(name: str) -> str:
    return str(Path.home() / ".local" / "share" / "zaribox" / "homes" / name)


def _run_package_install(
    backend: Backend, name: str, packages: list[str], image: str
) -> None:
    mgr = detect_pkgmgr(image)
    cmd = install_cmd(mgr)
    step(f"Installing {len(packages)} package(s) via {mgr}...")
    backend.exec(name, ["bash", "-c", cmd, "_", *packages], as_user=False)
    ok(f"Packages installed: {' '.join(packages)}")


def _run_package_remove(
    backend: Backend, name: str, packages: list[str], image: str
) -> None:
    mgr = detect_pkgmgr(image)
    cmd = remove_cmd(mgr)
    step(f"Removing {len(packages)} package(s): {' '.join(packages)}")
    backend.exec(name, ["bash", "-c", cmd, "_", *packages], as_user=False)
    ok(f"Removed: {' '.join(packages)}")


def run_apply(yaml_arg: str | None) -> int:
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

    name = config.name
    home_dir = config.home_dir or _default_home(name)
    desired_packages = config.packages
    state = StateStore()
    state.save_yaml_path(name, yaml_path.resolve())

    current_id_hash = container_identity_hash(config)
    old_id_hash = state.saved_container_hash(name)

    print()
    print(f"{BOLD}{CYN}ZariBox apply{RST}  {DIM}vpython{RST}")
    print(f"  {DIM}config  {RST}{yaml_path}")
    print(f"  {DIM}name    {RST}{name}")
    print(f"  {DIM}image   {RST}{config.image}")
    print()

    try:
        container_exists = backend.container_exists(name)
        needs_recreate = not container_exists or current_id_hash != old_id_hash

        if container_exists and current_id_hash != old_id_hash:
            warn(
                f"Container config changed (Image/HomeDir/ExtraFlags) -- recreating '{name}'..."
            )

        if needs_recreate:
            if container_exists:
                step("Stopping and removing old container...")
                try:
                    backend.stop(name)
                except RuntimeError:
                    pass
                try:
                    backend.rm(name)
                except RuntimeError:
                    pass
                ok("Old container removed")
            else:
                step(f"Creating new container '{name}'...")

            home = Path(home_dir)
            home.mkdir(parents=True, exist_ok=True)
            home.chmod(0o700)

            step("Pulling image and creating container...")
            backend.create(
                name,
                config.image,
                home_dir,
                config.extra_flags,
            )
            ok("Container created")

            state.save_container_hash(name, current_id_hash)
            state.save_packages(name, [])

            if desired_packages:
                _run_package_install(backend, name, desired_packages, config.image)
                state.save_packages(name, desired_packages)
            else:
                warn(f"No packages listed in {yaml_path}")

        else:
            ok(f"Container '{name}' is up to date -- checking packages...")
            saved_packages = state.saved_packages(name)
            to_install, to_remove = package_drift(desired_packages, saved_packages)

            if not to_install and not to_remove:
                ok("Packages are in sync -- nothing to do.")
                print()
                return 0

            if to_install:
                step(
                    f"Installing {len(to_install)} new package(s): {' '.join(to_install)}"
                )
                _run_package_install(backend, name, to_install, config.image)

            if to_remove:
                _run_package_remove(backend, name, to_remove, config.image)

            state.save_packages(name, desired_packages)

        if needs_recreate and config.run:
            if backend_name == "podman":
                step("Normalizing home directory ownership for user commands...")
                backend.fix_home_permissions(name, home_dir)

            step("Running post-install commands...")
            for command_line in config.run:
                step(f"  $ {command_line}")
                backend.exec(
                    name,
                    ["bash", "-c", command_line],
                    as_user=True,
                    capture_output=False,
                )
            ok("Post-install commands done")

    except RuntimeError as exc:
        err(str(exc))
        return 1

    print()
    ok(f"{BOLD}Done.{RST} Container '{name}' is ready.")
    log(f"Run: zaribox enter {yaml_path}")
    print()
    return 0
