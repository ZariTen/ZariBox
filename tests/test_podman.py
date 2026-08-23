from pathlib import Path

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
