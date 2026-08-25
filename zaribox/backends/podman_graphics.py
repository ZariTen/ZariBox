from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..state import StateStore
from .podman_mounts import mount_options

X11_SOCKET_DIR = Path("/tmp/.X11-unix")
RUNTIME_DIR_ROOT = Path("/run/user")


def _runtime_directory() -> Path | None:
    configured = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_dir() else None

    fallback = RUNTIME_DIR_ROOT / str(os.getuid())
    return fallback if fallback.is_dir() else None


def _resolve_display() -> str:
    display = os.environ.get("DISPLAY", "").strip()
    if display:
        return display

    if not X11_SOCKET_DIR.is_dir():
        return ""

    candidates = [
        socket_path
        for socket_path in X11_SOCKET_DIR.glob("X*")
        if socket_path.name.removeprefix("X").isdigit() and socket_path.is_socket()
    ]
    if len(candidates) == 1:
        display_number = candidates[0].name.removeprefix("X")
        return f":{display_number}"
    return ""


def _resolve_wayland_display(runtime_dir: Path | None) -> str:
    configured = os.environ.get("WAYLAND_DISPLAY", "").strip()
    if configured:
        socket_path = Path(configured).expanduser()
        if not socket_path.is_absolute():
            if runtime_dir is None:
                return ""
            socket_path = runtime_dir / socket_path
        return configured if socket_path.is_socket() else ""

    if runtime_dir is None:
        return ""

    candidates = [
        socket_path
        for socket_path in runtime_dir.glob("wayland-*")
        if socket_path.is_socket()
    ]
    return candidates[0].name if len(candidates) == 1 else ""


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
    runtime_dir = _runtime_directory()
    display = _resolve_display()
    wayland_display = _resolve_wayland_display(runtime_dir)
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip()

    if not session_type:
        if wayland_display:
            session_type = "wayland"
        elif display:
            session_type = "x11"

    args = [
        "--env",
        f"DISPLAY={display}",
        "--env",
        f"WAYLAND_DISPLAY={wayland_display}",
        "--env",
        f"XDG_SESSION_TYPE={session_type}",
    ]
    if runtime_dir is not None:
        args.extend(["--env", f"XDG_RUNTIME_DIR={runtime_dir}"])
    return args


def add_create_args(args: list[str], name: str) -> None:
    """Append host graphics and session integration flags to ``podman create``."""
    mnt_rw_rslave = mount_options("rw,rslave")
    mnt_ro = mount_options("ro")
    runtime_dir = _runtime_directory()
    display = _resolve_display()

    if display:
        if X11_SOCKET_DIR.is_dir():
            args.extend(
                [
                    "--volume",
                    f"{X11_SOCKET_DIR}:{X11_SOCKET_DIR}:{mnt_rw_rslave}",
                ]
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

    if runtime_dir is not None:
        runtime_path = str(runtime_dir)
        args.extend(
            [
                "--env",
                f"XDG_RUNTIME_DIR={runtime_path}",
                "--volume",
                f"{runtime_path}:{runtime_path}:{mnt_rw_rslave}",
            ]
        )
        bus_path = runtime_dir / "bus"
        if bus_path.is_socket():
            args.extend(["--env", f"DBUS_SESSION_BUS_ADDRESS=unix:path={bus_path}"])
        elif os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            args.extend(
                [
                    "--env",
                    f"DBUS_SESSION_BUS_ADDRESS={os.environ['DBUS_SESSION_BUS_ADDRESS']}",
                ]
            )

        pulse_dir = runtime_dir / "pulse"
        if pulse_dir.is_dir():
            args.extend(["--volume", f"{pulse_dir}:{pulse_dir}:{mnt_rw_rslave}"])
            if os.environ.get("PULSE_SERVER"):
                args.extend(["--env", f"PULSE_SERVER={os.environ['PULSE_SERVER']}"])
            elif (pulse_dir / "native").is_socket():
                args.extend(["--env", f"PULSE_SERVER=unix:{pulse_dir}/native"])

    if Path("/dev/dri").exists():
        args.extend(["--device", "/dev/dri"])
    if Path("/dev/kfd").exists():
        args.extend(["--device", "/dev/kfd"])
    if Path("/etc/localtime").exists():
        args.extend(["--volume", "/etc/localtime:/etc/localtime:ro"])
