from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True, frozen=True)
class Mount:
    source: str
    target: str
    read_only: bool = False
    options: tuple[str, ...] = ()


@dataclass(slots=True)
class Metadata:
    name: str
    ttl: str | int | None = None
    labels: dict[str, str] = field(default_factory=dict[str, str])
    annotations: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(slots=True)
class Workspace:
    home_dir: str | None = None
    home_mount: bool = False
    mounts: list[Mount] = field(default_factory=list[Mount])


@dataclass(slots=True)
class Runtime:
    image: str = ""
    packages: list[str] = field(default_factory=list[str])
    run: list[str] = field(default_factory=list[str])
    env: dict[str, str] = field(default_factory=dict[str, str])
    workdir: str | None = None


@dataclass(slots=True)
class Resources:
    cpus: float | None = None
    memory: str | None = None
    pids_limit: int | None = None


@dataclass(slots=True)
class Security:
    network: str | None = None
    profile: str | None = None
    privileged: bool = False
    read_only_root_filesystem: bool = False


# Descriptive aliases make either naming style convenient for callers.
MountConfig = Mount
MetadataConfig = Metadata
WorkspaceConfig = Workspace
RuntimeConfig = Runtime
ResourcesConfig = Resources
SecurityConfig = Security


@dataclass(slots=True)
class ZariConfig:
    file_path: Path
    name: str
    image: str
    home_dir: str | None = None
    extra_flags: str = ""
    packages: list[str] = field(default_factory=list[str])
    run: list[str] = field(default_factory=list[str])
    home_mount: bool = False
    api_version: str | None = None
    kind: str | None = None
    metadata: Metadata | None = None
    workspace: Workspace = field(default_factory=Workspace)
    runtime: Runtime = field(default_factory=Runtime)
    resources: Resources = field(default_factory=Resources)
    security: Security = field(default_factory=Security)

    @property
    def mounts(self) -> list[Mount]:
        return self.workspace.mounts

    @property
    def env(self) -> dict[str, str]:
        return self.runtime.env

    @property
    def workdir(self) -> str | None:
        return self.runtime.workdir

    @property
    def network(self) -> str | None:
        return self.security.network

    @property
    def profile(self) -> str | None:
        return self.security.profile
