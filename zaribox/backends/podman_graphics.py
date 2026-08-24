from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..state import StateStore
from .podman_mounts import mount_options


def host_xauthority() -> Path | None:
    xauthority = os.environ.get("XAUTHORITY")
    if not xauthority:
        home = os.environ.get("HOME")
        if not home:
            return None
        xauthority = str(Path(home) / ".Xauthority")

    path = Path(xauthority).expanduser()
    return path if path.is_file() else None


def xauthority_path(name: str) -> Path:
    return StateStore(name).cache_dir / "xauth"


def persist_xauthority(name: str) -> Path | None:
    source = host_xauthority()
    if source is None:
        return None

    target = xauthority_path(name)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(source, target)
        _ = target.chmod(0o600)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to persist Xauthority file from '{source}'"
        ) from exc
    return target


def refresh_xauthority(name: str) -> None:
    target = xauthority_path(name)
    if not target.is_file():
        return

    source = host_xauthority()
    if source is None or source.resolve() == target.resolve():
        return

    try:
        _ = shutil.copyfile(source, target)
        _ = target.chmod(0o600)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to refresh Xauthority file from '{source}'"
        ) from exc


def current_graphics_env() -> list[str]:
    display = os.environ.get("DISPLAY", "")
    args = ["--env", f"DISPLAY={display}"]

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    if (
        runtime_dir
        and wayland_display
        and Path(runtime_dir, wayland_display).is_socket()
    ):
        args.extend(["--env", f"WAYLAND_DISPLAY={wayland_display}"])
    else:
        args.extend(["--env", "WAYLAND_DISPLAY="])
    return args


def add_create_args(args: list[str], name: str) -> None:
    """Append host graphics and session integration flags to ``podman create``."""
    mnt_rw_rslave = mount_options("rw,rslave")
    mnt_ro = mount_options("ro")

    display = os.environ.get("DISPLAY")
    if display:
        if Path("/tmp/.X11-unix").is_dir():
            args.extend(
                ["--volume", f"/tmp/.X11-unix:/tmp/.X11-unix:{mnt_rw_rslave}"]
            )
        xauth = persist_xauthority(name)
        if xauth is not None:
            args.extend(
                [
                    "--env",
                    "XAUTHORITY=/tmp/.container_xauth",
                    "--volume",
                    f"{xauth}:/tmp/.container_xauth:{mnt_ro}",
                ]
            )

    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime_dir and Path(xdg_runtime_dir).is_dir():
        args.extend(
            [
                "--env",
                f"XDG_RUNTIME_DIR={xdg_runtime_dir}",
                "--volume",
                f"{xdg_runtime_dir}:{xdg_runtime_dir}:{mnt_rw_rslave}",
            ]
        )
        bus_path = Path(xdg_runtime_dir, "bus")
        if bus_path.is_socket():
            args.extend(["--env", f"DBUS_SESSION_BUS_ADDRESS=unix:path={bus_path}"])
        elif os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            args.extend(
                [
                    "--env",
                    f"DBUS_SESSION_BUS_ADDRESS={os.environ['DBUS_SESSION_BUS_ADDRESS']}",
                ]
            )

        pulse_dir = Path(xdg_runtime_dir, "pulse")
        if pulse_dir.is_dir():
            args.extend(["--volume", f"{pulse_dir}:{pulse_dir}:{mnt_rw_rslave}"])
            if os.environ.get("PULSE_SERVER"):
                args.extend(["--env", f"PULSE_SERVER={os.environ['PULSE_SERVER']}"])
            elif Path(pulse_dir, "native").is_socket():
                args.extend(["--env", f"PULSE_SERVER=unix:{pulse_dir}/native"])

    if Path("/dev/dri").exists():
        args.extend(["--device", "/dev/dri"])
    if Path("/dev/kfd").exists():
        args.extend(["--device", "/dev/kfd"])
    if Path("/etc/localtime").exists():
        args.extend(["--volume", "/etc/localtime:/etc/localtime:ro"])
