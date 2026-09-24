from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..shell import CommandResult, command_exists, run_command
from .podman_args import user_exec_args
from .podman_create import (
    CreatePolicy,
    MountSpec,
    build_create_command,
    resolve_create_policy,
)
from .podman_exec import build_exec_args, login_shell_command
from .podman_graphics import current_graphics_env, refresh_xauthority
from .podman_mounts import mount_options, mounted_workdir, parse_mounts
from .podman_users import machine_id_setup_script, user_setup_script

__all__ = ["CreatePolicy", "MountSpec", "PodmanBackend"]

_CREATE_MAX_OUTPUT_BYTES = 1_048_576


def _raise_on_failure(result: CommandResult, context: str) -> None:
    if result.returncode != 0:
        stderr_text = result.stderr.strip()
        message = f"{context} failed"
        if stderr_text:
            message = f"{message}\n{stderr_text}"
        raise RuntimeError(message)


def _run(
    args: Sequence[str],
    *,
    capture_output: bool = True,
    timeout: float | None = None,
    max_output_bytes: int | None = None,
) -> CommandResult:
    # Only forward limits that are set; test doubles of run_command accept a
    # narrower signature than the real one.
    limits: dict[str, float | int] = {}
    if timeout is not None:
        limits["timeout"] = timeout
    if max_output_bytes is not None:
        limits["max_output_bytes"] = max_output_bytes
    return run_command(args, capture_output=capture_output, **limits)  # type: ignore[arg-type]


class PodmanBackend:
    name: str = "podman"

    def __init__(self) -> None:
        self._runtime_seen: bool | None = None
        self._host_identity: tuple[int, int, str] | None = None
        self._home_cache: dict[str, str] = {}
        self._agent_cache: dict[str, bool] = {}

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

    def _host_home(self, default: str) -> str:
        return os.environ.get("HOME", default).rstrip("/")

    def _is_rootless(self) -> bool:
        return self._get_host_identity()[0] != 0

    def _podman(self, *args: str) -> CommandResult:
        result = run_command(["podman", *args], capture_output=True)
        _raise_on_failure(result, f"podman {args[0]}")
        return result

    def _inspect(self, name: str, fmt: str) -> str | None:
        result = run_command(
            ["podman", "inspect", "--format", fmt, name], capture_output=True
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _container_home(self, name: str) -> str:
        if name not in self._home_cache:
            self._home_cache[name] = self.label(name, "io.zaribox.home") or ""
        return self._home_cache[name]

    def _container_mounts(self, name: str) -> list[tuple[Path, Path]]:
        """Return host-to-container paths for mounts on a container."""
        output = self._inspect(
            name, "{{range .Mounts}}{{.Source}}\t{{.Destination}}\n{{end}}"
        )
        return parse_mounts(output) if output else []

    def _is_agent_container(self, name: str) -> bool:
        if name not in self._agent_cache:
            self._agent_cache[name] = (
                self.label(name, "io.zaribox.security-profile") == "agent"
            )
        return self._agent_cache[name]

    def _resolve_agent(self, name: str, agent_mode: bool | None) -> bool:
        return self._is_agent_container(name) if agent_mode is None else agent_mode

    def _start_if_needed(self, name: str, *, agent_mode: bool | None = None) -> None:
        if not self._resolve_agent(name, agent_mode):
            refresh_xauthority(name)
        self._podman("start", name)
        self._ensure_machine_id(name)

    def _exec_in_container(self, name: str, cmd: str) -> CommandResult:
        return run_command(
            ["podman", "exec", "--user", "0", name, "sh", "-c", cmd],
            capture_output=True,
        )

    def _ensure_machine_id(self, name: str) -> None:
        result = self._exec_in_container(name, machine_id_setup_script())
        _raise_on_failure(result, f"Failed to initialize machine ID in '{name}'")

    def _user_exists(self, name: str, uid: int) -> bool:
        return self._exec_in_container(name, f"getent passwd {uid}").returncode == 0

    def _ensure_user(
        self, name: str, home_dir: str, *, allow_passwordless_sudo: bool = True
    ) -> None:
        host_uid, host_gid, host_user = self._get_host_identity()
        self._start_if_needed(name)

        script = user_setup_script(
            uid=host_uid,
            gid=host_gid,
            user=host_user,
            home_dir=home_dir,
            allow_passwordless_sudo=allow_passwordless_sudo,
        )
        result = self._exec_in_container(name, script)
        _raise_on_failure(
            result, f"Failed to initialize user inside container '{name}'"
        )

    def container_exists(self, name: str) -> bool:
        return (
            self.runtime_present()
            and run_command(
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
        *,
        policy: CreatePolicy | None = None,
        security_profile: str | None = None,
        network: str | None = None,
        mounts: Sequence[MountSpec] | None = None,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        resource_limits: Mapping[str, str | int | float] | None = None,
        read_only_root: bool | None = None,
        writable_tmpfs: Sequence[str] | None = None,
    ) -> None:
        selected = resolve_create_policy(
            policy=policy,
            extra_flags=extra_flags,
            security_profile=security_profile,
            network=network,
            mounts=mounts,
            env=env,
            workdir=workdir,
            resource_limits=resource_limits,
            read_only_root=read_only_root,
            writable_tmpfs=writable_tmpfs,
        )
        os.makedirs(home_dir, exist_ok=True)
        home_dir = home_dir.rstrip("/")
        _, _, host_user = self._get_host_identity()
        args = build_create_command(
            name=name,
            image=image,
            home_dir=home_dir,
            host_user=host_user,
            host_actual_home=self._host_home(f"/home/{host_user}"),
            home_mount=home_mount,
            rootless=self._is_rootless(),
            extra_flags=extra_flags,
            policy=selected,
            mount_options=mount_options,
        )
        timeout = selected.command_timeout
        create_result = _run(
            args,
            timeout=timeout,
            max_output_bytes=_CREATE_MAX_OUTPUT_BYTES if timeout is not None else None,
        )
        _raise_on_failure(create_result, "podman create")

        self._agent_cache[name] = selected.agent_mode
        self._start_if_needed(name, agent_mode=selected.agent_mode)
        if not (selected.agent_mode and selected.read_only_root):
            self._ensure_user(
                name, home_dir, allow_passwordless_sudo=not selected.agent_mode
            )

    def exec(
        self,
        name: str,
        command: Sequence[str],
        *,
        as_user: bool = False,
        check: bool = True,
        capture_output: bool = True,
        timeout: float | None = None,
        max_output_bytes: int | None = None,
        agent_mode: bool | None = None,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        is_agent = self._resolve_agent(name, agent_mode)
        self._start_if_needed(name, agent_mode=is_agent)

        if as_user:
            host_uid, host_gid, host_user = self._get_host_identity()
            user_args = user_exec_args(
                host_uid, host_gid, host_user, self._container_home(name)
            )
        else:
            user_args = ["--user", "0"]

        args = build_exec_args(
            name,
            command,
            user_args=user_args,
            workdir=workdir,
            env=env,
            graphics_env=[] if is_agent else current_graphics_env(),
        )
        result = _run(
            args,
            capture_output=capture_output,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
        )
        if check:
            _raise_on_failure(result, "podman exec")
        return result

    def enter(self, name: str, current_dir: Path | None = None) -> int:
        preferred_shell = Path(os.environ.get("SHELL", "/bin/sh")).name
        host_uid, host_gid, host_user = self._get_host_identity()
        home_dir = self._container_home(name) or os.environ.get("HOME", "/")

        self._start_if_needed(name)
        if not self._user_exists(name, host_uid):
            self._ensure_user(
                name,
                home_dir,
                allow_passwordless_sudo=not self._is_agent_container(name),
            )

        host_workdir = current_dir if current_dir is not None else Path.cwd()
        exec_args = build_exec_args(
            name,
            ["sh", "-lc", login_shell_command(preferred_shell)],
            user_args=user_exec_args(host_uid, host_gid, host_user, home_dir),
            workdir=mounted_workdir(host_workdir, self._container_mounts(name)),
            graphics_env=current_graphics_env(),
            interactive=True,
        )
        return subprocess.run(exec_args, check=False).returncode

    def post_install(self, name: str, home_dir: str) -> None:
        host_uid, host_gid, _ = self._get_host_identity()
        target_home = home_dir.rstrip("/")

        if target_home in ("", self._host_home("")) or self._is_rootless():
            return

        _ = self._exec_in_container(
            name, f"chown {host_uid}:{host_gid} {shlex.quote(target_home)}"
        )

    def start(self, name: str) -> None:
        self._start_if_needed(name)

    def stop(self, name: str) -> None:
        self._podman("stop", name)

    def rm(self, name: str) -> None:
        self._podman("rm", "-f", name)

    def rename(self, name: str, new_name: str) -> None:
        self._podman("rename", name, new_name)
        if name in self._home_cache:
            self._home_cache[new_name] = self._home_cache.pop(name)
        if name in self._agent_cache:
            self._agent_cache[new_name] = self._agent_cache.pop(name)

    def label(self, name: str, key: str) -> str | None:
        return self._inspect(name, f'{{{{ index .Config.Labels "{key}" }}}}') or None

    def image_digest(self, name: str) -> str | None:
        return self._inspect(name, "{{.ImageDigest}}") or None

    def is_running(self, name: str) -> bool:
        return (self._inspect(name, "{{.State.Running}}") or "").lower() == "true"

    def detect_package_manager(self, name: str) -> str:
        from ..pkgmgr import probe_cmd

        result = self.exec(name, probe_cmd(), as_user=False, check=False)
        manager = result.stdout.strip()
        if result.returncode != 0 or not manager:
            raise RuntimeError(f"No supported package manager found in '{name}'")
        return manager

    def ps(self) -> str:
        return self._podman("ps", "-a").stdout
