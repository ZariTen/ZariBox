import shlex
import subprocess

import pytest

from zaribox.backends.podman_args import (
    env_args,
    identity_env_args,
    user_exec_args,
    volume_args,
)
from zaribox.backends.podman_exec import build_exec_args, login_shell_command


def test_build_exec_args_minimal() -> None:
    assert build_exec_args("box", ["true"], user_args=["--user", "0"]) == [
        "podman",
        "exec",
        "--user",
        "0",
        "box",
        "true",
    ]


def test_build_exec_args_full_ordering() -> None:
    args = build_exec_args(
        "box",
        ["sh", "-c", "echo hi"],
        user_args=["--user", "1000:1000"],
        workdir="/work",
        env={"FOO": "bar"},
        graphics_env=["--env", "DISPLAY=:0"],
        interactive=True,
    )

    assert args == [
        "podman",
        "exec",
        "-it",
        "--user",
        "1000:1000",
        "--workdir",
        "/work",
        "--env",
        "FOO=bar",
        "--env",
        "DISPLAY=:0",
        "box",
        "sh",
        "-c",
        "echo hi",
    ]


def test_build_exec_args_skips_empty_workdir() -> None:
    args = build_exec_args("box", ["true"], user_args=[], workdir="")
    assert "--workdir" not in args


def test_build_exec_args_rejects_relative_workdir() -> None:
    with pytest.raises(ValueError, match="absolute path"):
        build_exec_args("box", ["true"], user_args=[], workdir="relative/dir")


def test_build_exec_args_options_come_before_container_name() -> None:
    # Anything after the container name is passed to the command, not podman.
    args = build_exec_args(
        "box",
        ["true"],
        user_args=["--user", "0"],
        env={"A": "1"},
        graphics_env=["--env", "DISPLAY=:0"],
    )
    name_index = args.index("box")
    assert all(arg.startswith("--") for arg in args[2:name_index:2])
    assert args[name_index + 1 :] == ["true"]


def test_login_shell_command_prefers_requested_shell() -> None:
    command = login_shell_command("zsh")
    assert command.startswith("if command -v zsh >/dev/null 2>&1; then exec zsh -l;")
    assert "exec bash -l" in command
    assert command.endswith("else exec sh -l; fi")


def test_login_shell_command_quotes_shell_name() -> None:
    command = login_shell_command("evil; rm -rf /")
    quoted = shlex.quote("evil; rm -rf /")
    assert f"command -v {quoted} " in command
    assert f"exec {quoted} -l" in command


def test_login_shell_command_is_valid_sh_and_falls_back() -> None:
    # A shell that does not exist must fall back to bash or sh, not fail.
    script = login_shell_command("zaribox-missing-shell").replace(" -l", " -c 'exit 7'")
    result = subprocess.run(["sh", "-c", script], check=False)
    assert result.returncode == 7


def test_env_args_preserves_order() -> None:
    assert env_args({"B": "2", "A": "1"}) == ["--env", "B=2", "--env", "A=1"]
    assert env_args({}) == []


def test_volume_args_formats_bind_mount() -> None:
    assert volume_args("/src", "/dst", "ro,z") == ["--volume", "/src:/dst:ro,z"]


def test_user_exec_args_sets_identity() -> None:
    assert user_exec_args(1000, 100, "zari", "/home/zari") == [
        "--user",
        "1000:100",
        *identity_env_args("zari", "/home/zari"),
    ]
    assert identity_env_args("zari", "/home/zari") == [
        "--env",
        "USER=zari",
        "--env",
        "LOGNAME=zari",
        "--env",
        "HOME=/home/zari",
    ]
