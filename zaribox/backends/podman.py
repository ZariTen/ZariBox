from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

from ..shell import CommandResult, command_exists, run_command
from .podman_graphics import add_create_args, current_graphics_env, refresh_xauthority
from .podman_mounts import mount_options, mounted_workdir, parse_mounts


class PodmanBackend:
    name: str = "podman"

    def __init__(self) -> None:
        self._runtime_seen: bool | None = None
        self._host_identity: tuple[int, int, str] | None = None
        self._home_cache: dict[str, str] = {}

    def runtime_present(self) -> bool:
        if self._runtime_seen is None:
            self._runtime_seen = command_exists("podman")
        return self._runtime_seen

    def _get_host_identity(self) -> tuple[int, int, str]:
        if self._host_identity is None:
            uid = os.getuid()
            user = os.environ.get("USER") or str(uid)
            self._host_identity = (uid, os.getgid(), user)
        return self._host_identity

    def _raise_on_failure(self, result: CommandResult, context: str) -> None:
        if result.returncode != 0:
            stderr_text = result.stderr.strip()
            message = f"{context} failed"
            if stderr_text:
                message = f"{message}\n{stderr_text}"
            raise RuntimeError(message)

    def _mount_opts(self, opts: str) -> str:
        return mount_options(opts)

    def _is_rootless(self) -> bool:
        return self._get_host_identity()[0] != 0

    def _container_home(self, name: str) -> str:
        if name not in self._home_cache:
            result = run_command(
                [
                    "podman",
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "io.zaribox.home" }}',
                    name,
                ],
                capture_output=True,
            )
            self._home_cache[name] = (
                result.stdout.strip() if result.returncode == 0 else ""
            )
        return self._home_cache[name]

    def _container_mounts(self, name: str) -> list[tuple[Path, Path]]:
        """Return host-to-container paths for mounts on a container."""
        result = run_command(
            [
                "podman",
                "inspect",
                "--format",
                "{{range .Mounts}}{{.Source}}\t{{.Destination}}\n{{end}}",
                name,
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            return []
        return parse_mounts(result.stdout)

    def _current_graphics_env(self) -> list[str]:
        return current_graphics_env()

    def _start_if_needed(self, name: str) -> None:
        refresh_xauthority(name)
        result = run_command(["podman", "start", name], capture_output=True)
        self._raise_on_failure(result, "podman start")

    def _exec_in_container(self, name: str, cmd: str) -> CommandResult:
        return run_command(
            ["podman", "exec", "--user", "0", name, "sh", "-c", cmd],
            capture_output=True,
        )

    def _user_exists(self, name: str, uid: int) -> bool:
        result = self._exec_in_container(name, f"getent passwd {uid}")
        return result.returncode == 0

    def _ensure_user(self, name: str, home_dir: str) -> None:
        host_uid, host_gid, host_user = self._get_host_identity()
        self._start_if_needed(name)

        script = f"""
        getent group {host_gid} >/dev/null 2>&1 ||
            groupadd -g {host_gid} {shlex.quote(host_user)} 2>/dev/null ||
            addgroup -g {host_gid} {shlex.quote(host_user)}
        getent passwd {host_uid} >/dev/null 2>&1 ||
            useradd -M -d {shlex.quote(home_dir)} -u {host_uid} -g {host_gid} {shlex.quote(host_user)} 2>/dev/null ||
            adduser -H -h {shlex.quote(home_dir)} -u {host_uid} -G {shlex.quote(host_user)} -D {shlex.quote(host_user)}

        mkdir -p /etc/sudoers.d
        printf '%s ALL=(ALL:ALL) NOPASSWD:ALL\n' {shlex.quote(host_user)} > /etc/sudoers.d/90-zaribox-user
        chmod 0440 /etc/sudoers.d/90-zaribox-user
        """
        result = self._exec_in_container(name, script)
        self._raise_on_failure(
            result, f"Failed to initialize user inside container '{name}'"
        )

    def container_exists(self, name: str) -> bool:
        if not self.runtime_present():
            return False
        return (
            run_command(
                ["podman", "container", "exists", name], capture_output=True
            ).returncode
            == 0
        )

    def create(
        self,
        name: str,
        image: str,
        home_dir: str,
        extra_flags: str = "",
        home_mount: bool = False,
    ) -> None:
        os.makedirs(home_dir, exist_ok=True)
        home_dir = home_dir.rstrip("/")
        _, _, host_user = self._get_host_identity()

        mnt_home = self._mount_opts("rslave")
        host_actual_home = os.environ.get("HOME", f"/home/{host_user}").rstrip("/")

        args = [
            "podman",
            "create",
            "--name",
            name,
            "--hostname",
            name,
            "--label",
            "io.zaribox.managed=true",
            "--label",
            f"io.zaribox.home={home_dir}",
            "--security-opt",
            "label=disable",
            "--network",
            "host",
            "--ipc",
            "host",
            "--env",
            f"HOME={home_dir}",
            "--env",
            f"USER={host_user}",
            "--env",
            f"LOGNAME={host_user}",
            "--workdir",
            home_dir,
            "--volume",
            f"{home_dir}:{home_dir}:{mnt_home}",
        ]

        if home_mount and host_actual_home != home_dir:
            args.extend(
                [
                    "--volume",
                    f"{host_actual_home}:{host_actual_home}:{self._mount_opts('rw')}",
                ]
            )

        if (
            host_actual_home
            and not home_dir.startswith(host_actual_home + "/")
            and host_actual_home != home_dir
            and not home_mount
        ):
            args.extend(
                ["--volume", f"{host_actual_home}:{host_actual_home}:{mnt_home}"]
            )

        if self._is_rootless():
            args.extend(["--userns", "keep-id"])

        term = os.environ.get("TERM")
        if term:
            args.extend(["--env", f"TERM={term}"])

        add_create_args(args, name)

        if extra_flags.strip():
            args.extend(shlex.split(extra_flags))

        args.extend([image, "sleep", "infinity"])

        create_result = run_command(args, capture_output=False)
        self._raise_on_failure(create_result, "podman create")

        self._start_if_needed(name)
        self._ensure_user(name, home_dir)

    def exec(
        self,
        name: str,
        command: Sequence[str],
        *,
        as_user: bool = False,
        check: bool = True,
        capture_output: bool = True,
    ) -> CommandResult:
        self._start_if_needed(name)
        args = ["podman", "exec"]

        if as_user:
            host_uid, host_gid, host_user = self._get_host_identity()
            home_dir = self._container_home(name)
            args.extend(
                [
                    "--user",
                    f"{host_uid}:{host_gid}",
                    "--env",
                    f"USER={host_user}",
                    "--env",
                    f"LOGNAME={host_user}",
                    "--env",
                    f"HOME={home_dir}",
                ]
            )
        else:
            args.extend(["--user", "0"])

        args.extend([*self._current_graphics_env(), name, *command])
        result = run_command(args, capture_output=capture_output)
        if check and result.returncode != 0:
            self._raise_on_failure(result, "podman exec")
        return result

    def enter(self, name: str, current_dir: Path | None = None) -> int:
        preferred_shell = Path(os.environ.get("SHELL", "/bin/sh")).name
        host_uid, host_gid, host_user = self._get_host_identity()
        home_dir = self._container_home(name) or os.environ.get("HOME", "/")

        self._start_if_needed(name)
        if not self._user_exists(name, host_uid):
            self._ensure_user(name, home_dir)

        host_workdir = current_dir if current_dir is not None else Path.cwd()
        container_workdir = mounted_workdir(host_workdir, self._container_mounts(name))
        shell_cmd = (
            f"if command -v {shlex.quote(preferred_shell)} >/dev/null 2>&1; then exec {shlex.quote(preferred_shell)} -l; "
            f"elif command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh -l; fi"
        )
        exec_args = [
            "podman",
            "exec",
            "-it",
            "--user",
            f"{host_uid}:{host_gid}",
            "--env",
            f"USER={host_user}",
            "--env",
            f"LOGNAME={host_user}",
            "--env",
            f"HOME={home_dir}",
        ]
        if container_workdir is not None:
            exec_args.extend(["--workdir", container_workdir])
        exec_args.extend(
            [
                *self._current_graphics_env(),
                name,
                "sh",
                "-lc",
                shell_cmd,
            ]
        )
        result = subprocess.run(exec_args, check=False)
        return result.returncode

    def post_install(self, name: str, home_dir: str) -> None:
        host_uid, host_gid, _ = self._get_host_identity()
        host_actual_home = os.environ.get("HOME", "").rstrip("/")
        target_home = home_dir.rstrip("/")

        if target_home == host_actual_home or target_home == "" or self._is_rootless():
            return

        _ = self._exec_in_container(
            name, f"chown {host_uid}:{host_gid} {shlex.quote(target_home)}"
        )

    def stop(self, name: str) -> None:
        self._raise_on_failure(
            run_command(["podman", "stop", name], capture_output=True), "podman stop"
        )

    def rm(self, name: str) -> None:
        self._raise_on_failure(
            run_command(["podman", "rm", "-f", name], capture_output=True), "podman rm"
        )

    def ps(self) -> str:
        result = run_command(["podman", "ps", "-a"], capture_output=True)
        self._raise_on_failure(result, "podman ps")
        return result.stdout
