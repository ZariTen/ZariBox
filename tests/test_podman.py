import socket
from pathlib import Path

import pytest

from zaribox.backends.podman import PodmanBackend
from zaribox.shell import CommandResult


def test_create_home_mount_adds_host_home_volume(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run_command(
        args: list[str], *, capture_output: bool = True, check: bool = False
    ) -> CommandResult:
        del capture_output, check
        command = list(args)
        commands.append(command)
        return CommandResult(command, 0, "", "")

    monkeypatch.setattr("zaribox.backends.podman.run_command", fake_run_command)
    monkeypatch.setenv("USER", "zariuser")
    monkeypatch.setenv("HOME", "")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    backend = PodmanBackend()
    backend.create(
        "box",
        "archlinux:latest",
        str(tmp_path / "container-home"),
        home_mount=True,
    )

    create_args = next(
        command for command in commands if command[:2] == ["podman", "create"]
    )
    mount = [
        create_args[index + 1]
        for index, argument in enumerate(create_args[:-1])
        if argument == "--volume"
    ]
    assert "/home/zariuser:/run/host/home/zariuser:rw" in mount


def test_create_persists_xauthority_outside_session_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def fake_run_command(
        args: list[str], *, capture_output: bool = True, check: bool = False
    ) -> CommandResult:
        del capture_output, check
        command = list(args)
        commands.append(command)
        return CommandResult(command, 0, "", "")

    source = tmp_path / "runtime" / "xauth_session"
    source.parent.mkdir()
    source.write_text("cookie", encoding="utf-8")
    home_dir = tmp_path / "container-home"

    monkeypatch.setattr("zaribox.backends.podman.run_command", fake_run_command)
    monkeypatch.setenv("USER", "zariuser")
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XAUTHORITY", str(source))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    backend = PodmanBackend()
    backend.create("box", "archlinux:latest", str(home_dir))

    create_args = next(
        command for command in commands if command[:2] == ["podman", "create"]
    )
    mounts = [
        create_args[index + 1]
        for index, argument in enumerate(create_args[:-1])
        if argument == "--volume"
    ]
    persistent = tmp_path / "config" / "zaribox" / "box" / "xauth"

    assert persistent.read_text(encoding="utf-8") == "cookie"
    assert "DISPLAY=:0" not in create_args
    assert f"{persistent}:/tmp/.container_xauth:ro" in mounts
    assert f"{source}:/tmp/.container_xauth:ro" not in mounts

    source.unlink()
    assert persistent.is_file()


def test_start_refreshes_persistent_xauthority(
    monkeypatch, tmp_path: Path
) -> None:
    config_home = tmp_path / "config"
    persistent = config_home / "zaribox" / "box" / "xauth"
    persistent.parent.mkdir(parents=True)
    persistent.write_text("old-cookie", encoding="utf-8")
    source = tmp_path / "runtime" / "xauth_session"
    source.parent.mkdir()
    source.write_text("new-cookie", encoding="utf-8")

    def fake_run_command(
        args: list[str], *, capture_output: bool = True, check: bool = False
    ) -> CommandResult:
        del capture_output, check
        if args[:2] == ["podman", "inspect"]:
            return CommandResult(args, 0, "", "")
        return CommandResult(args, 0, "", "")

    monkeypatch.setattr("zaribox.backends.podman.run_command", fake_run_command)
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    monkeypatch.setenv("XAUTHORITY", str(source))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    PodmanBackend()._start_if_needed("box")

    assert persistent.read_text(encoding="utf-8") == "new-cookie"


def test_wayland_only_does_not_mount_xauthority(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run_command(
        args: list[str], *, capture_output: bool = True, check: bool = False
    ) -> CommandResult:
        del capture_output, check
        command = list(args)
        commands.append(command)
        return CommandResult(command, 0, "", "")

    source = tmp_path / "runtime" / "xauth_session"
    source.parent.mkdir()
    source.write_text("cookie", encoding="utf-8")

    monkeypatch.setattr("zaribox.backends.podman.run_command", fake_run_command)
    monkeypatch.setenv("USER", "zariuser")
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("XAUTHORITY", str(source))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    PodmanBackend().create("box", "archlinux:latest", str(tmp_path / "home"))

    create_args = next(
        command for command in commands if command[:2] == ["podman", "create"]
    )
    assert "XAUTHORITY=/tmp/.container_xauth" not in create_args
    assert all(str(source) not in argument for argument in create_args)


def test_exec_uses_current_wayland_display(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    wayland_socket = runtime_dir / "wayland-1"
    socket_file = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_file.bind(str(wayland_socket))

    commands: list[list[str]] = []

    def fake_run_command(
        args: list[str], *, capture_output: bool = True, check: bool = False
    ) -> CommandResult:
        del capture_output, check
        command = list(args)
        commands.append(command)
        return CommandResult(command, 0, "", "")

    try:
        monkeypatch.setattr(
            "zaribox.backends.podman.run_command", fake_run_command
        )
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

        PodmanBackend().exec("box", ["true"], as_user=False)
    finally:
        socket_file.close()
        wayland_socket.unlink()

    exec_args = next(
        command for command in commands if command[:2] == ["podman", "exec"]
    )
    assert "DISPLAY=:0" in exec_args
    assert "WAYLAND_DISPLAY=wayland-1" in exec_args


def test_start_failure_is_reported(monkeypatch, tmp_path: Path) -> None:
    def fake_run_command(
        args: list[str], *, capture_output: bool = True, check: bool = False
    ) -> CommandResult:
        del capture_output, check
        if args[:2] == ["podman", "start"]:
            return CommandResult(
                args,
                125,
                "",
                "crun: cannot stat /run/user/1000/xauth_missing",
            )
        return CommandResult(args, 0, "", "")

    monkeypatch.setattr("zaribox.backends.podman.run_command", fake_run_command)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    with pytest.raises(
        RuntimeError,
        match=r"podman start failed\ncrun: cannot stat /run/user/1000/xauth_missing",
    ):
        PodmanBackend()._start_if_needed("box")
