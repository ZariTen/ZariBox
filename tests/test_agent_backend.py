from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from zaribox.backends.podman import CreatePolicy, MountSpec, PodmanBackend
from zaribox.pkgmgr import install_cmd
from zaribox.shell import CommandResult, run_command


def _record_podman(monkeypatch, tmp_path: Path) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run_command(
        args: list[str],
        *,
        capture_output: bool = True,
        check: bool = False,
        **kwargs: object,
    ) -> CommandResult:
        del capture_output, check, kwargs
        command = list(args)
        commands.append(command)
        return CommandResult(command, 0, "", "")

    monkeypatch.setattr("zaribox.backends.podman.run_command", fake_run_command)
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    return commands


def test_home_is_not_implicitly_mounted_when_home_mount_is_false(
    monkeypatch, tmp_path: Path
) -> None:
    commands = _record_podman(monkeypatch, tmp_path)
    container_home = tmp_path / "container-home"

    PodmanBackend().create("box", "image", str(container_home))

    create = next(
        command for command in commands if command[:2] == ["podman", "create"]
    )
    volumes = [
        create[index + 1] for index, value in enumerate(create) if value == "--volume"
    ]
    assert all(
        not volume.startswith(f"{tmp_path / 'host-home'}:") for volume in volumes
    )
    assert any(
        volume.startswith(f"{container_home}:{container_home}:") for volume in volumes
    )


def test_agent_policy_is_isolated_and_structured(monkeypatch, tmp_path: Path) -> None:
    commands = _record_podman(monkeypatch, tmp_path)
    source = tmp_path / "source"
    policy = CreatePolicy(
        security_profile="agent",
        mounts=(MountSpec(source, "/workspace", "ro"),),
        env={"CI": "true"},
        workdir="/workspace",
        resource_limits={"cpus": 1.5, "memory": "512m", "pids_limit": 128},
        read_only_root=True,
        writable_tmpfs=("/tmp:rw,size=64m",),
    )

    PodmanBackend().create("agent", "image", str(tmp_path / "home"), policy=policy)

    create = next(
        command for command in commands if command[:2] == ["podman", "create"]
    )
    assert create[create.index("--network") + 1] == "none"
    assert create[create.index("--ipc") + 1] == "private"
    assert ["--cap-drop", "all"] == create[
        create.index("--cap-drop") : create.index("--cap-drop") + 2
    ]
    assert "no-new-privileges" in create
    assert "--read-only" in create
    assert "/tmp:rw,size=64m" in create
    assert f"{source}:/workspace:ro" in create
    assert "CI=true" in create
    assert create[create.index("--workdir") + 1] == "/workspace"
    assert "--cpus" in create and "1.5" in create
    assert "--memory" in create and "512m" in create
    assert not any("container_xauth" in value for value in create)


def test_writable_agent_user_is_not_granted_passwordless_sudo(
    monkeypatch, tmp_path: Path
) -> None:
    commands = _record_podman(monkeypatch, tmp_path)
    policy = CreatePolicy(security_profile="agent")

    PodmanBackend().create(
        "agent", "image", str(tmp_path / "home"), policy=policy
    )

    user_setup = next(
        command
        for command in commands
        if command[:5] == ["podman", "exec", "--user", "0", "agent"]
        and "getent passwd" in command[-1]
    )
    assert "sudoers" not in user_setup[-1]


def test_agent_policy_rejects_unsafe_escape_hatches(
    monkeypatch, tmp_path: Path
) -> None:
    _record_podman(monkeypatch, tmp_path)
    backend = PodmanBackend()

    with pytest.raises(ValueError, match="extra_flags"):
        backend.create(
            "agent",
            "image",
            str(tmp_path / "home"),
            "--privileged",
            security_profile="agent",
        )
    with pytest.raises(ValueError, match="host networking"):
        backend.create(
            "agent",
            "image",
            str(tmp_path / "home"),
            security_profile="agent",
            network="host",
        )


def test_agent_exec_has_no_graphics_forwarding(monkeypatch, tmp_path: Path) -> None:
    commands = _record_podman(monkeypatch, tmp_path)
    monkeypatch.setenv("DISPLAY", ":0")

    PodmanBackend().exec("agent", ["true"], agent_mode=True)

    execute = next(command for command in commands if command[:2] == ["podman", "exec"])
    assert not any(value.startswith("DISPLAY=") for value in execute)


def test_command_result_old_construction_and_output_limit() -> None:
    old = CommandResult(["true"], 0, "", "")
    assert old.timed_out is False
    assert old.truncated is False

    result = run_command(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000)"],
        max_output_bytes=64,
    )
    assert result.returncode == 0
    assert len(result.stdout.encode()) == 64
    assert result.truncated is True
    assert result.timed_out is False


def test_run_command_timeout_is_reported() -> None:
    started = time.monotonic()
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        timeout=0.05,
        max_output_bytes=64,
    )
    assert time.monotonic() - started < 2
    assert result.returncode == 124
    assert result.timed_out is True


def test_unknown_image_package_command_probes_in_container() -> None:
    command = install_cmd("auto")
    assert "command -v pacman" in command
    assert "command -v apt-get" in command
    assert "No supported package manager found" in command
