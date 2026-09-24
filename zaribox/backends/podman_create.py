from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .podman_args import env_args, identity_env_args, volume_args
from .podman_graphics import add_create_args


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


@dataclass(frozen=True, slots=True)
class ResolvedCreatePolicy:
    security_profile: str
    network: str
    mounts: Sequence[MountSpec]
    env: Mapping[str, str]
    workdir: str | None
    resource_limits: Mapping[str, str | int | float]
    read_only_root: bool
    writable_tmpfs: Sequence[str]
    labels: Mapping[str, str]
    command_timeout: float | None

    @property
    def agent_mode(self) -> bool:
        return self.security_profile == "agent"


_RESOURCE_FLAGS = {
    "cpus": "--cpus",
    "memory": "--memory",
    "memory_swap": "--memory-swap",
    "pids_limit": "--pids-limit",
    "cpuset_cpus": "--cpuset-cpus",
}
_RESERVED_LABELS = {
    "io.zaribox.managed",
    "io.zaribox.home",
    "io.zaribox.security-profile",
}


def resolve_create_policy(
    *,
    policy: CreatePolicy | None,
    extra_flags: str,
    security_profile: str | None,
    network: str | None,
    mounts: Sequence[MountSpec] | None,
    env: Mapping[str, str] | None,
    workdir: str | None,
    resource_limits: Mapping[str, str | int | float] | None,
    read_only_root: bool | None,
    writable_tmpfs: Sequence[str] | None,
) -> ResolvedCreatePolicy:
    selected = policy or CreatePolicy()
    profile = security_profile or selected.security_profile
    if profile not in {"default", "agent"}:
        raise ValueError("security_profile must be 'default' or 'agent'")

    agent_mode = profile == "agent"
    if agent_mode and extra_flags.strip():
        raise ValueError("extra_flags are not allowed with the agent security profile")

    selected_network = network if network is not None else selected.network
    if agent_mode:
        selected_network = selected_network or "none"
        if selected_network == "host":
            raise ValueError("host networking is not allowed in agent mode")
    else:
        selected_network = selected_network or "host"

    selected_limits = (
        resource_limits if resource_limits is not None else selected.resource_limits
    )
    unknown_limits = set(selected_limits) - set(_RESOURCE_FLAGS)
    if unknown_limits:
        raise ValueError(
            f"Unsupported resource limit(s): {', '.join(sorted(unknown_limits))}"
        )

    return ResolvedCreatePolicy(
        security_profile=profile,
        network=selected_network,
        mounts=mounts if mounts is not None else selected.mounts,
        env=env if env is not None else selected.env,
        workdir=workdir if workdir is not None else selected.workdir,
        resource_limits=selected_limits,
        read_only_root=(
            read_only_root if read_only_root is not None else selected.read_only_root
        ),
        writable_tmpfs=(
            writable_tmpfs if writable_tmpfs is not None else selected.writable_tmpfs
        ),
        labels=selected.labels,
        command_timeout=selected.command_timeout,
    )


def build_create_command(
    *,
    name: str,
    image: str,
    home_dir: str,
    host_user: str,
    host_actual_home: str,
    home_mount: bool,
    rootless: bool,
    extra_flags: str,
    policy: ResolvedCreatePolicy,
    mount_options: Callable[[str], str],
) -> list[str]:
    agent_mode = policy.agent_mode
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
        f"io.zaribox.security-profile={policy.security_profile}",
        "--network",
        policy.network,
        "--ipc",
        "private" if agent_mode else "host",
        *identity_env_args(host_user, home_dir),
        "--workdir",
        policy.workdir or home_dir,
        *volume_args(home_dir, home_dir, mount_options("rslave")),
    ]

    if agent_mode:
        args.extend(["--cap-drop", "all", "--security-opt", "no-new-privileges"])
    else:
        args.extend(["--security-opt", "label=disable"])
    if policy.read_only_root:
        args.append("--read-only")
    for tmpfs in policy.writable_tmpfs:
        args.extend(["--tmpfs", tmpfs])
    for key, value in policy.resource_limits.items():
        args.extend([_RESOURCE_FLAGS[key], str(value)])
    for mount in policy.mounts:
        args.extend(
            volume_args(mount.source, mount.target, mount_options(mount.options))
        )
    args.extend(env_args(policy.env))
    for key, value in policy.labels.items():
        if key in _RESERVED_LABELS:
            raise ValueError(f"Reserved container label: {key}")
        args.extend(["--label", f"{key}={value}"])

    if home_mount and host_actual_home != home_dir:
        args.extend(
            volume_args(host_actual_home, host_actual_home, mount_options("rw"))
        )
    if rootless:
        args.extend(["--userns", "keep-id"])

    term = os.environ.get("TERM")
    if term:
        args.extend(["--env", f"TERM={term}"])
    if not agent_mode:
        add_create_args(args, name)
    if extra_flags.strip():
        args.extend(shlex.split(extra_flags))

    args.extend([image, "sleep", "infinity"])
    return args
