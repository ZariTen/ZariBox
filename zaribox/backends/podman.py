from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Sequence

from ..shell import CommandResult, command_exists, run_command
from .base import Backend


class PodmanBackend(Backend):
    name = "podman"

    def runtime_present(self) -> bool:
        return command_exists("podman")

    def _raise_on_failure(self, result: CommandResult, context: str) -> None:
        if result.returncode != 0:
            stderr_text = result.stderr.strip()
            message = f"{context} failed"
            if stderr_text:
                message = f"{message}\n{stderr_text}"
            raise RuntimeError(message)

    def _selinux_enabled(self) -> bool:
        if command_exists("getenforce"):
            result = run_command(["getenforce"], capture_output=True)
            if result.returncode == 0:
                return result.stdout.strip() != "Disabled"
        return Path("/sys/fs/selinux/enforce").exists()

    def _mount_opts(self, opts: str) -> str:
        if (
            self._selinux_enabled()
            and os.environ.get("ZARIBOX_PODMAN_RELABEL", "0") == "1"
        ):
            return f"{opts},z"
        return opts

    def _is_rootless(self) -> bool:
        result = run_command(
            ["podman", "info", "--format", "{{.Host.Security.Rootless}}"],
            capture_output=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _container_home(self, name: str) -> str:
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
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _start_if_needed(self, name: str) -> None:
        run_command(["podman", "start", name], capture_output=True)

    def _exec_in_container(self, name: str, cmd: str) -> CommandResult:
        return run_command(
            ["podman", "exec", "--user", "0", name, "sh", "-c", cmd],
            capture_output=True,
        )

    def _ensure_user(self, name: str, home_dir: str) -> None:
        host_uid = os.getuid()
        host_gid = os.getgid()
        host_user = os.environ.get("USER") or str(host_uid)

        self._start_if_needed(name)

        # Check and create group
        result = self._exec_in_container(name, f"getent group {host_gid}")
        if result.returncode != 0:
            self._exec_in_container(
                name,
                f"groupadd -g {host_gid} {shlex.quote(host_user)} 2>/dev/null || "
                f"addgroup -g {host_gid} {shlex.quote(host_user)} 2>/dev/null || true",
            )

        # Check and create user
        result = self._exec_in_container(name, f"getent passwd {host_uid}")
        if result.returncode != 0:
            self._exec_in_container(
                name,
                f"useradd -m -d {shlex.quote(home_dir)} -u {host_uid} -g {host_gid} {shlex.quote(host_user)} 2>/dev/null || "
                f"adduser -D -h {shlex.quote(home_dir)} -u {host_uid} -G {shlex.quote(host_user)} {shlex.quote(host_user)} 2>/dev/null || true",
            )

        # Setup sudo if available and user exists
        result = self._exec_in_container(name, "command -v sudo")
        if result.returncode == 0:
            sudoers_content = f"{host_user} ALL=(ALL:ALL) NOPASSWD:ALL"
            self._exec_in_container(
                name,
                f"mkdir -p /etc/sudoers.d && "
                f"echo {shlex.quote(sudoers_content)} > /etc/sudoers.d/90-zaribox-user && "
                f"chmod 0440 /etc/sudoers.d/90-zaribox-user || true",
            )

        self._exec_in_container(
            name,
            f"chown -R {host_uid}:{host_gid} {shlex.quote(home_dir)} 2>/dev/null || true",
        )

    def container_exists(self, name: str) -> bool:
        if not self.runtime_present():
            return False

        result = run_command(
            ["podman", "container", "inspect", name], capture_output=True
        )
        return result.returncode == 0

    def _resolved_extra_flag_tokens(self, extra_flags: str) -> list[str]:
        extra_flag_tokens = shlex.split(extra_flags) if extra_flags.strip() else []

        return extra_flag_tokens

    def create(
        self,
        name: str,
        image: str,
        home_dir: str,
        extra_flags: str = "",
    ) -> None:
        host_user = os.environ.get("USER") or str(os.getuid())
        mnt_rw_rslave = self._mount_opts("rw,rslave")
        mnt_ro = self._mount_opts("ro")
        mnt_home = self._mount_opts("rslave")
        extra_flag_tokens = self._resolved_extra_flag_tokens(extra_flags)

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

        if self._is_rootless():
            args.extend(["--userns", "keep-id"])

        term = os.environ.get("TERM")
        if term:
            args.extend(["--env", f"TERM={term}"])

        display = os.environ.get("DISPLAY")
        if display:
            args.extend(["--env", f"DISPLAY={display}"])
            if Path("/tmp/.X11-unix").is_dir():
                args.extend(
                    ["--volume", f"/tmp/.X11-unix:/tmp/.X11-unix:{mnt_rw_rslave}"]
                )

            xauthority = os.environ.get("XAUTHORITY")
            home = os.environ.get("HOME", "")
            if xauthority and Path(xauthority).is_file():
                args.extend(
                    [
                        "--env",
                        f"XAUTHORITY={xauthority}",
                        "--volume",
                        f"{xauthority}:{xauthority}:{mnt_ro}",
                    ]
                )
            elif home and Path(home, ".Xauthority").is_file():
                auth = str(Path(home, ".Xauthority"))
                args.extend(
                    [
                        "--env",
                        f"XAUTHORITY={auth}",
                        "--volume",
                        f"{auth}:{auth}:{mnt_ro}",
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

            wayland_display = os.environ.get("WAYLAND_DISPLAY")
            if wayland_display and Path(xdg_runtime_dir, wayland_display).is_socket():
                args.extend(["--env", f"WAYLAND_DISPLAY={wayland_display}"])

            bus_path = Path(xdg_runtime_dir, "bus")
            if bus_path.is_socket():
                args.extend(["--env", f"DBUS_SESSION_BUS_ADDRESS=unix:path={bus_path}"])
            else:
                dbus_addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
                if dbus_addr:
                    args.extend(["--env", f"DBUS_SESSION_BUS_ADDRESS={dbus_addr}"])

            pulse_dir = Path(xdg_runtime_dir, "pulse")
            if pulse_dir.is_dir():
                args.extend(["--volume", f"{pulse_dir}:{pulse_dir}:{mnt_rw_rslave}"])
                pulse_server = os.environ.get("PULSE_SERVER")
                if pulse_server:
                    args.extend(["--env", f"PULSE_SERVER={pulse_server}"])
                elif Path(pulse_dir, "native").is_socket():
                    args.extend(["--env", f"PULSE_SERVER=unix:{pulse_dir}/native"])

        if Path("/dev/dri").exists():
            args.extend(["--device", "/dev/dri"])
        if Path("/dev/kfd").exists():
            args.extend(["--device", "/dev/kfd"])

        if Path("/etc/localtime").exists():
            args.extend(["--volume", "/etc/localtime:/etc/localtime:ro"])

        if extra_flag_tokens:
            args.extend(extra_flag_tokens)

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
            host_uid = os.getuid()
            host_gid = os.getgid()
            host_user = os.environ.get("USER") or str(host_uid)
            args.extend(
                [
                    "--user",
                    f"{host_uid}:{host_gid}",
                    "--env",
                    f"USER={host_user}",
                    "--env",
                    f"LOGNAME={host_user}",
                ]
            )
        else:
            args.extend(["--user", "0"])

        args.extend([name, *command])
        result = run_command(args, capture_output=capture_output)
        if check and result.returncode != 0:
            self._raise_on_failure(result, "podman exec")
        return result

    def fix_home_permissions(self, name: str, home_dir: str) -> None:
        host_uid = os.getuid()
        host_gid = os.getgid()

        self._start_if_needed(name)
        self._ensure_user(name, home_dir)
        result = run_command(
            [
                "podman",
                "exec",
                name,
                "sh",
                "-lc",
                f"chown -R {host_uid}:{host_gid} {shlex.quote(home_dir)}",
            ],
            capture_output=True,
        )
        self._raise_on_failure(result, "podman fix home permissions")

    def enter(self, name: str) -> int:
        preferred_shell = Path(os.environ.get("SHELL", "/bin/sh")).name
        host_uid = os.getuid()
        host_gid = os.getgid()
        host_user = os.environ.get("USER") or str(host_uid)

        home_dir = self._container_home(name) or os.environ.get("HOME", "/")
        self._start_if_needed(name)
        self._ensure_user(name, home_dir)

        shell_cmd = (
            f"if command -v {shlex.quote(preferred_shell)} >/dev/null 2>&1; then "
            f"exec {shlex.quote(preferred_shell)} -l; "
            "elif command -v bash >/dev/null 2>&1; then exec bash -l; "
            "else exec sh -l; fi"
        )
        result = subprocess.run(
            [
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
                name,
                "sh",
                "-lc",
                shell_cmd,
            ],
            check=False,
        )
        return result.returncode

    def stop(self, name: str) -> None:
        result = run_command(["podman", "stop", name], capture_output=True)
        self._raise_on_failure(result, "podman stop")

    def rm(self, name: str) -> None:
        result = run_command(["podman", "rm", "-f", name], capture_output=True)
        self._raise_on_failure(result, "podman rm")

    def ps(self) -> str:
        result = run_command(["podman", "ps", "-a"], capture_output=True)
        self._raise_on_failure(result, "podman ps")
        return result.stdout
