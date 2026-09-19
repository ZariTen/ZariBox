from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..shell import CommandResult, command_exists, run_command
from .podman_graphics import add_create_args, current_graphics_env, refresh_xauthority
from .podman_mounts import mount_options, mounted_workdir, parse_mounts


@dataclass(frozen=True, slots=True)
class MountSpec:
    source: str | Path
    target: str | Path
    options: str = "rw"


@dataclass(frozen=True, slots=True)
class CreatePolicy:
    """Structured Podman create options; ``agent`` enables the hardened profile."""

    security_profile: str = "default"
    network: str | None = None
    mounts: Sequence[MountSpec] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    workdir: str | None = None
    resource_limits: Mapping[str, str | int | float] = field(default_factory=dict)
    read_only_root: bool = False
    writable_tmpfs: Sequence[str] = ()
    labels: Mapping[str, str] = field(default_factory=dict)
    command_timeout: float | None = None


_RESOURCE_FLAGS = {
    "cpus": "--cpus",
    "memory": "--memory",
    "memory_swap": "--memory-swap",
    "pids_limit": "--pids-limit",
    "cpuset_cpus": "--cpuset-cpus",
}


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

    def _is_agent_container(self, name: str) -> bool:
        if name not in self._agent_cache:
            result = run_command(
                [
                    "podman",
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "io.zaribox.security-profile" }}',
                    name,
                ],
                capture_output=True,
            )
            self._agent_cache[name] = (
                result.returncode == 0 and result.stdout.strip() == "agent"
            )
        return self._agent_cache[name]

    def _start_if_needed(self, name: str, *, agent_mode: bool | None = None) -> None:
        is_agent = self._is_agent_container(name) if agent_mode is None else agent_mode
        if not is_agent:
            refresh_xauthority(name)
        result = run_command(["podman", "start", name], capture_output=True)
        self._raise_on_failure(result, "podman start")
        self._ensure_machine_id(name)

    def _exec_in_container(self, name: str, cmd: str) -> CommandResult:
        return run_command(
            ["podman", "exec", "--user", "0", name, "sh", "-c", cmd],
            capture_output=True,
        )

    def _ensure_machine_id(self, name: str) -> None:
        script = """
        if [ ! -s /etc/machine-id ]; then
            if command -v systemd-machine-id-setup >/dev/null 2>&1; then
                systemd-machine-id-setup >/dev/null 2>&1 || true
            fi
            if [ ! -s /etc/machine-id ] && [ -r /proc/sys/kernel/random/uuid ]; then
                tr -d '-' < /proc/sys/kernel/random/uuid > /etc/machine-id 2>/dev/null || true
            fi
        fi
        """
        result = self._exec_in_container(name, script)
        self._raise_on_failure(result, f"Failed to initialize machine ID in '{name}'")

    def _user_exists(self, name: str, uid: int) -> bool:
        result = self._exec_in_container(name, f"getent passwd {uid}")
        return result.returncode == 0

    def _ensure_user(
        self, name: str, home_dir: str, *, allow_passwordless_sudo: bool = True
    ) -> None:
        host_uid, host_gid, host_user = self._get_host_identity()
        self._start_if_needed(name)

        sudo_setup = ""
        if allow_passwordless_sudo:
            sudo_setup = f"""
        mkdir -p /etc/sudoers.d
        printf '%s ALL=(ALL:ALL) NOPASSWD:ALL\\n' {shlex.quote(host_user)} > /etc/sudoers.d/90-zaribox-user
        chmod 0440 /etc/sudoers.d/90-zaribox-user
            """
        script = f"""
        getent group {host_gid} >/dev/null 2>&1 ||
            groupadd -g {host_gid} {shlex.quote(host_user)} 2>/dev/null ||
            addgroup -g {host_gid} {shlex.quote(host_user)}
        getent passwd {host_uid} >/dev/null 2>&1 ||
            useradd -M -d {shlex.quote(home_dir)} -u {host_uid} -g {host_gid} {shlex.quote(host_user)} 2>/dev/null ||
            adduser -H -h {shlex.quote(home_dir)} -u {host_uid} -G {shlex.quote(host_user)} -D {shlex.quote(host_user)}
        {sudo_setup}
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
        selected = policy or CreatePolicy()
        profile = security_profile or selected.security_profile
        if profile not in {"default", "agent"}:
            raise ValueError("security_profile must be 'default' or 'agent'")
        agent_mode = profile == "agent"
        if agent_mode and extra_flags.strip():
            raise ValueError(
                "extra_flags are not allowed with the agent security profile"
            )

        selected_network = network if network is not None else selected.network
        if agent_mode:
            selected_network = selected_network or "none"
            if selected_network == "host":
                raise ValueError("host networking is not allowed in agent mode")
        else:
            selected_network = selected_network or "host"

        selected_mounts = mounts if mounts is not None else selected.mounts
        selected_env = env if env is not None else selected.env
        selected_workdir = workdir if workdir is not None else selected.workdir
        selected_limits = (
            resource_limits if resource_limits is not None else selected.resource_limits
        )
        selected_read_only = (
            read_only_root if read_only_root is not None else selected.read_only_root
        )
        selected_tmpfs = (
            writable_tmpfs if writable_tmpfs is not None else selected.writable_tmpfs
        )
        selected_labels = selected.labels

        unknown_limits = set(selected_limits) - set(_RESOURCE_FLAGS)
        if unknown_limits:
            raise ValueError(
                f"Unsupported resource limit(s): {', '.join(sorted(unknown_limits))}"
            )

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
            "--label",
            f"io.zaribox.security-profile={profile}",
            "--network",
            selected_network,
            "--ipc",
            "private" if agent_mode else "host",
            "--env",
            f"HOME={home_dir}",
            "--env",
            f"USER={host_user}",
            "--env",
            f"LOGNAME={host_user}",
            "--workdir",
            selected_workdir or home_dir,
            "--volume",
            f"{home_dir}:{home_dir}:{mnt_home}",
        ]

        if agent_mode:
            args.extend(["--cap-drop", "all", "--security-opt", "no-new-privileges"])
        else:
            args.extend(["--security-opt", "label=disable"])
        if selected_read_only:
            args.append("--read-only")
        for tmpfs in selected_tmpfs:
            args.extend(["--tmpfs", tmpfs])
        for key, value in selected_limits.items():
            args.extend([_RESOURCE_FLAGS[key], str(value)])
        for mount in selected_mounts:
            args.extend(
                [
                    "--volume",
                    f"{mount.source}:{mount.target}:{self._mount_opts(mount.options)}",
                ]
            )
        for key, value in selected_env.items():
            args.extend(["--env", f"{key}={value}"])
        for key, value in selected_labels.items():
            if key in {
                "io.zaribox.managed",
                "io.zaribox.home",
                "io.zaribox.security-profile",
            }:
                raise ValueError(f"Reserved container label: {key}")
            args.extend(["--label", f"{key}={value}"])

        if home_mount and host_actual_home != home_dir:
            args.extend(
                [
                    "--volume",
                    f"{host_actual_home}:{host_actual_home}:{self._mount_opts('rw')}",
                ]
            )
        if self._is_rootless():
            args.extend(["--userns", "keep-id"])

        term = os.environ.get("TERM")
        if term:
            args.extend(["--env", f"TERM={term}"])
        if not agent_mode:
            add_create_args(args, name)
        if extra_flags.strip():
            args.extend(shlex.split(extra_flags))

        args.extend([image, "sleep", "infinity"])
        create_options: dict[str, object] = {"capture_output": True}
        if selected.command_timeout is not None:
            create_options["timeout"] = selected.command_timeout
            create_options["max_output_bytes"] = 1_048_576
        create_result = run_command(args, **create_options)  # type: ignore[arg-type]
        self._raise_on_failure(create_result, "podman create")

        self._agent_cache[name] = agent_mode
        self._start_if_needed(name, agent_mode=agent_mode)
        if not (agent_mode and selected_read_only):
            self._ensure_user(
                name, home_dir, allow_passwordless_sudo=not agent_mode
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
        is_agent = self._is_agent_container(name) if agent_mode is None else agent_mode
        self._start_if_needed(name, agent_mode=is_agent)
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

        if workdir:
            if not workdir.startswith("/"):
                raise ValueError("Container workdir must be an absolute path")
            args.extend(["--workdir", workdir])
        for key, value in (env or {}).items():
            args.extend(["--env", f"{key}={value}"])

        graphics_env = [] if is_agent else self._current_graphics_env()
        args.extend([*graphics_env, name, *command])
        run_options: dict[str, object] = {"capture_output": capture_output}
        if timeout is not None:
            run_options["timeout"] = timeout
        if max_output_bytes is not None:
            run_options["max_output_bytes"] = max_output_bytes
        result = run_command(args, **run_options)  # type: ignore[arg-type]
        if check and result.returncode != 0:
            self._raise_on_failure(result, "podman exec")
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

    def start(self, name: str) -> None:
        self._start_if_needed(name)

    def stop(self, name: str) -> None:
        self._raise_on_failure(
            run_command(["podman", "stop", name], capture_output=True), "podman stop"
        )

    def rm(self, name: str) -> None:
        self._raise_on_failure(
            run_command(["podman", "rm", "-f", name], capture_output=True), "podman rm"
        )

    def rename(self, name: str, new_name: str) -> None:
        self._raise_on_failure(
            run_command(["podman", "rename", name, new_name], capture_output=True),
            "podman rename",
        )
        if name in self._home_cache:
            self._home_cache[new_name] = self._home_cache.pop(name)
        if name in self._agent_cache:
            self._agent_cache[new_name] = self._agent_cache.pop(name)

    def label(self, name: str, key: str) -> str | None:
        result = run_command(
            [
                "podman",
                "inspect",
                "--format",
                f'{{{{ index .Config.Labels "{key}" }}}}',
                name,
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    def image_digest(self, name: str) -> str | None:
        result = run_command(
            [
                "podman",
                "inspect",
                "--format",
                "{{.ImageDigest}}",
                name,
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    def is_running(self, name: str) -> bool:
        result = run_command(
            ["podman", "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def detect_package_manager(self, name: str) -> str:
        from ..pkgmgr import probe_cmd

        result = self.exec(name, probe_cmd(), as_user=False, check=False)
        manager = result.stdout.strip()
        if result.returncode != 0 or not manager:
            raise RuntimeError(f"No supported package manager found in '{name}'")
        return manager

    def detect_pkgmgr(self, name: str) -> str:
        """Backward-friendly short alias for container package-manager probing."""
        return self.detect_package_manager(name)

    def ps(self) -> str:
        result = run_command(["podman", "ps", "-a"], capture_output=True)
        self._raise_on_failure(result, "podman ps")
        return result.stdout
