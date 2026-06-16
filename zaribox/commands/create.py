from __future__ import annotations

import os
from pathlib import Path

from ..config import load_context
from ..logging import err, log, ok, step, warn
from ..state import StateStore, container_identity_hash
from .apply import _sync_from_config


def _default_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _default_home(name: str) -> str:
    return str(_default_data_dir() / "zaribox" / "home" / name)


def run_create(yaml_arg: str) -> int:
    try:
        yaml_path, config, backend_name, backend = load_context(yaml_arg)
    except (ValueError, RuntimeError) as exc:
        err(str(exc))
        return 1

    if not backend.runtime_present():
        err(f"{backend_name} is not installed or not in PATH.")
        return 1

    name = config.name
    home_dir = config.home_dir or _default_home(name)
    state = StateStore(name)

    if backend.container_exists(name):
        warn(f"Container '{name}' already exists — recreating...")
        try:
            backend.stop(name)
        except RuntimeError:
            pass
        try:
            backend.rm(name)
        except RuntimeError:
            pass
        ok("Old container removed")

    home = Path(home_dir)
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)

    step(f"Pulling image and creating container '{name}'...")
    try:
        backend.create(name, config.image, home_dir, config.extra_flags)
    except RuntimeError as exc:
        err(str(exc))
        return 1

    ok("Container created")
    state.save_yaml_path(name, yaml_path.resolve())
    state.save_container_hash(name, container_identity_hash(config))
    state.save_packages(name, [])

    result = _sync_from_config(config, backend, backend_name)
    if result != 0:
        return result

    if config.run:
        step("Running post-install commands...")
        try:
            for command_line in config.run:
                step(f"  $ {command_line}")
                backend.exec(
                    name,
                    ["bash", "-c", command_line],
                    as_user=True,
                    capture_output=False,
                )
            backend.post_install(name, home_dir)
        except RuntimeError as exc:
            err(str(exc))
            return 1
        ok("Post-install commands done")

    ok(f"Container '{name}' is ready.")
    log(f"Run: zaribox enter {name}")
    return 0
