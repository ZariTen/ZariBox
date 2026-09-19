from __future__ import annotations

import os
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .backends import PodmanBackend, make_backend
from .backends.podman import CreatePolicy, MountSpec
from .config import load_config, resolve_backend, resolve_yaml
from .models import ZariConfig
from .pkgmgr import detect_pkgmgr, install_cmd, remove_cmd
from .project_state import (
    ProjectRecord,
    ProjectStateStore,
    cleanup_expired_sessions,
)
from .shell import CommandResult
from .state import StateStore, container_identity_hash, package_drift


@dataclass(frozen=True, slots=True)
class PlanAction:
    kind: str
    destructive: bool = False
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanResult:
    project_id: str
    container: str
    config_path: str
    actions: tuple[PlanAction, ...]
    requires_force: bool
    identity_digest: str


@dataclass(frozen=True, slots=True)
class InspectionResult:
    project_id: str
    container: str
    config_path: str
    exists: bool
    config_in_sync: bool
    desired_packages: tuple[str, ...]
    applied_packages: tuple[str, ...]
    install: tuple[str, ...]
    remove: tuple[str, ...]
    image: str
    image_digest: str | None
    security_profile: str
    expires_at: float | None


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation_id: str
    changed: bool
    container: str
    actions: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecResult:
    container: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def result_dict(value: object) -> dict[str, Any]:
    return asdict(value)  # type: ignore[arg-type]


def _ttl_seconds(value: str | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, int):
        return float(value)
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    total = 0.0
    number = ""
    for char in value:
        if char.isdigit() or char == ".":
            number += char
        else:
            total += float(number) * units[char]
            number = ""
    return total


def _default_home(name: str) -> str:
    configured = os.environ.get("XDG_DATA_HOME")
    base = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".local" / "share"
    )
    return str(base / "zaribox" / "home" / name)


class ZariBoxService:
    """Programmatic, non-interactive orchestration boundary for CLI and agents."""

    def __init__(self, backend: PodmanBackend | None = None) -> None:
        self._backend = backend

    def validate(self, yaml_arg: str | Path | None) -> ZariConfig:
        path = resolve_yaml(yaml_arg)
        config = load_config(path)
        resolve_backend(config)
        self._validate_policy(config)
        self._validate_agent_home(config)
        self._mount_specs(config)
        return config

    def _validate_policy(self, config: ZariConfig) -> None:
        profile = self._profile(config)
        if config.kind == "AgentBox" and profile != "agent":
            raise ValueError(
                "AgentBox manifests cannot select a desktop/default profile"
            )
        if profile == "agent":
            if config.network == "host":
                raise ValueError(
                    "Host networking is not allowed with the agent security profile"
                )
            if config.home_mount:
                raise ValueError(
                    "HomeMount is not allowed with the agent security profile"
                )
            if config.extra_flags:
                raise ValueError(
                    "ExtraFlags are not allowed with the agent security profile"
                )
            if config.security.privileged:
                raise ValueError(
                    "Privileged containers are not allowed with the agent security profile"
                )
            if config.security.read_only_root_filesystem and (
                config.packages or config.run
            ):
                raise ValueError(
                    "ReadOnlyRootFilesystem cannot be combined with Packages or Run; "
                    "use a prebuilt image"
                )
        elif config.security.privileged:
            raise ValueError(
                "Security.Privileged is not supported; use an explicit trusted Podman workflow"
            )

    def _profile(self, config: ZariConfig) -> str:
        profile = (
            config.profile or ("agent" if config.kind == "AgentBox" else "default")
        ).lower()
        if config.kind == "AgentBox":
            if profile not in {"restricted", "agent"}:
                raise ValueError(
                    "AgentBox Security.Profile must be agent or restricted"
                )
            return "agent"
        if profile in {"restricted", "agent"}:
            return "agent"
        if profile in {"default", "desktop"}:
            return "default"
        raise ValueError(
            "Security.Profile must be one of: agent, restricted, desktop, default"
        )

    def _backend_for(self, config: ZariConfig) -> PodmanBackend:
        return self._backend or make_backend(resolve_backend(config))

    def _resolve_config_for_name(self, name: str) -> Path:
        legacy = StateStore(name).yaml_path_for(name)
        if legacy is not None and legacy.is_file():
            return legacy
        root = ProjectStateStore(Path.cwd() / "placeholder.yaml").root / "projects"
        if root.is_dir():
            for state_path in root.glob("*/state.json"):
                try:
                    import json

                    raw = json.loads(state_path.read_text(encoding="utf-8"))
                    candidate = Path(str(raw.get("config_path", ""))).expanduser()
                    if raw.get("container_name") == name and candidate.is_file():
                        return candidate
                except (OSError, ValueError, TypeError):
                    continue
        raise ValueError(f"No managed configuration found for container '{name}'")

    def config_for_target(self, target: str | Path) -> ZariConfig:
        direct = Path(target).expanduser()
        path = (
            direct if direct.is_file() else self._resolve_config_for_name(str(target))
        )
        config = load_config(path)
        self._validate_policy(config)
        self._validate_agent_home(config)
        self._mount_specs(config)
        return config

    def _record(
        self, config: ZariConfig
    ) -> tuple[ProjectStateStore, ProjectRecord | None]:
        store = ProjectStateStore(config.file_path)
        record = store.load()
        if record is None:
            legacy = StateStore(config.name)
            legacy_hash = legacy.saved_container_hash(config.name)
            legacy_path = legacy.yaml_path_for(config.name)
            if (
                legacy_hash
                and legacy_path is not None
                and legacy_path.resolve() == config.file_path.resolve()
            ):
                record = ProjectRecord(
                    project_id=store.project_id,
                    config_path=str(config.file_path.resolve()),
                    container_name=config.name,
                    backend=resolve_backend(config),
                    applied_identity_digest=legacy_hash,
                    applied_packages=legacy.saved_packages(config.name),
                    image=config.image,
                    security_profile=self._profile(config),
                )
        return store, record

    def inspect(self, target: str | Path) -> InspectionResult:
        config = self.config_for_target(target)
        backend = self._backend_for(config)
        store, record = self._record(config)
        applied = record.applied_packages if record else []
        install, remove = package_drift(config.packages, applied)
        identity = container_identity_hash(config)
        exists = backend.container_exists(config.name)
        digest = backend.image_digest(config.name) if exists else None
        return InspectionResult(
            project_id=store.project_id,
            container=config.name,
            config_path=str(config.file_path.resolve()),
            exists=exists,
            config_in_sync=bool(record and record.applied_identity_digest == identity),
            desired_packages=tuple(config.packages),
            applied_packages=tuple(applied),
            install=tuple(install),
            remove=tuple(remove),
            image=config.image,
            image_digest=digest,
            security_profile=self._profile(config),
            expires_at=record.expires_at if record else None,
        )

    def plan(self, yaml_arg: str | Path | None) -> PlanResult:
        config = self.validate(yaml_arg)
        return self._plan_config(config)

    def plan_target(self, target: str | Path) -> PlanResult:
        return self._plan_config(self.config_for_target(target))

    def _plan_config(self, config: ZariConfig) -> PlanResult:
        backend = self._backend_for(config)
        store, record = self._record(config)
        identity = container_identity_hash(config)
        exists = backend.container_exists(config.name)
        actions: list[PlanAction] = []
        if not exists:
            actions.append(PlanAction("create", details={"image": config.image}))
            applied: list[str] = []
        elif record is None or record.applied_identity_digest != identity:
            actions.append(
                PlanAction(
                    "recreate",
                    destructive=True,
                    details={
                        "from": record.applied_identity_digest if record else None,
                        "to": identity,
                    },
                )
            )
            applied = []
        else:
            applied = record.applied_packages
        install, remove = package_drift(config.packages, applied)
        if install:
            actions.append(
                PlanAction("install_packages", details={"packages": install})
            )
        if remove:
            actions.append(
                PlanAction(
                    "remove_packages", destructive=True, details={"packages": remove}
                )
            )
        if config.run and any(
            action.kind in {"create", "recreate"} for action in actions
        ):
            actions.append(
                PlanAction("run_post_install", details={"count": len(config.run)})
            )
        return PlanResult(
            store.project_id,
            config.name,
            str(config.file_path.resolve()),
            tuple(actions),
            any(action.destructive for action in actions),
            identity,
        )

    def _allowed_mount_roots(self, config: ZariConfig) -> list[Path]:
        configured = os.environ.get("ZARIBOX_ALLOWED_MOUNT_ROOTS", "")
        roots = [
            Path(item).expanduser().resolve()
            for item in configured.split(os.pathsep)
            if item
        ]
        return roots or [config.file_path.resolve().parent]

    def _validate_agent_home(self, config: ZariConfig) -> None:
        if self._profile(config) != "agent" or config.home_dir is None:
            return
        home = Path(config.home_dir).expanduser()
        if home.is_symlink():
            raise ValueError("Agent HomeDir must not be a symbolic link")
        resolved = home.resolve()
        host_home = Path.home().resolve()
        if resolved == host_home:
            raise ValueError("Agent HomeDir cannot be the host home directory")
        if not any(
            resolved == root or root in resolved.parents
            for root in self._allowed_mount_roots(config)
        ):
            raise ValueError(f"Agent HomeDir is outside allowed roots: {resolved}")

    def _mount_specs(self, config: ZariConfig) -> tuple[MountSpec, ...]:
        result: list[MountSpec] = []
        profile = self._profile(config)
        project_root = config.file_path.resolve().parent
        allowed_roots = self._allowed_mount_roots(config)
        for mount in config.mounts:
            source = Path(mount.source).expanduser()
            if not source.is_absolute():
                source = (project_root / source).resolve()
            else:
                source = source.resolve()
            if not source.exists():
                raise ValueError(f"Mount source does not exist: {source}")
            if profile == "agent" and not any(
                source == root or root in source.parents for root in allowed_roots
            ):
                raise ValueError(f"Agent mount is outside allowed roots: {source}")
            if profile == "agent":
                allowed_options = {"nodev", "nosuid", "noexec"}
                unsupported = set(mount.options) - allowed_options
                if unsupported:
                    raise ValueError(
                        "Unsafe AgentBox mount option(s): "
                        + ", ".join(sorted(unsupported))
                    )
            options = ["ro" if mount.read_only else "rw", *mount.options]
            result.append(
                MountSpec(source, mount.target, ",".join(dict.fromkeys(options)))
            )
        return tuple(result)

    def _provision_timeout(self) -> float:
        raw = os.environ.get("ZARIBOX_PROVISION_TIMEOUT", "900")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError("ZARIBOX_PROVISION_TIMEOUT must be numeric") from exc
        if value <= 0:
            raise ValueError("ZARIBOX_PROVISION_TIMEOUT must be positive")
        return value

    def _create_policy(self, config: ZariConfig) -> CreatePolicy:
        limits: dict[str, str | int | float] = {}
        profile = self._profile(config)
        limits["cpus"] = (
            config.resources.cpus if config.resources.cpus is not None else 2
        )
        limits["memory"] = (
            config.resources.memory if config.resources.memory is not None else "2g"
        )
        limits["pids_limit"] = (
            config.resources.pids_limit
            if config.resources.pids_limit is not None
            else 256
        )
        if profile != "agent":
            limits = {
                key: value
                for key, value in limits.items()
                if (
                    (key == "cpus" and config.resources.cpus is not None)
                    or (key == "memory" and config.resources.memory is not None)
                    or (key == "pids_limit" and config.resources.pids_limit is not None)
                )
            }
        read_only = config.security.read_only_root_filesystem
        tmpfs = ("/tmp:rw,nosuid,nodev,size=256m",) if read_only else ()
        return CreatePolicy(
            security_profile=profile,
            network=config.network,
            mounts=self._mount_specs(config),
            env=config.env,
            workdir=config.workdir,
            resource_limits=limits,
            read_only_root=read_only,
            writable_tmpfs=tmpfs,
            labels={
                "io.zaribox.project-id": ProjectStateStore(config.file_path).project_id,
                **(
                    {
                        f"io.zaribox.user-label.{key}": value
                        for key, value in config.metadata.labels.items()
                    }
                    if config.metadata
                    else {}
                ),
            },
            command_timeout=self._provision_timeout(),
        )

    def _assert_owned(
        self, backend: PodmanBackend, config: ZariConfig, store: ProjectStateStore
    ) -> None:
        if backend.label(config.name, "io.zaribox.managed") != "true":
            raise PermissionError(
                f"Container '{config.name}' is not managed by ZariBox"
            )
        owner = backend.label(config.name, "io.zaribox.project-id")
        if owner is not None and owner != store.project_id:
            raise PermissionError(
                f"Container '{config.name}' belongs to another ZariBox project"
            )
        if owner is None and self._profile(config) == "agent":
            raise PermissionError(
                f"Agent container '{config.name}' has no project ownership label; recreate it"
            )

    def _sync_packages(
        self, backend: PodmanBackend, config: ZariConfig, applied: list[str]
    ) -> list[str]:
        install, remove = package_drift(config.packages, applied)
        manager = detect_pkgmgr(config.image)
        timeout = self._provision_timeout()
        if install:
            backend.exec(
                config.name,
                ["sh", "-c", install_cmd(manager), "_", *install],
                as_user=False,
                timeout=timeout,
                max_output_bytes=4_194_304,
            )
        if remove:
            backend.exec(
                config.name,
                ["sh", "-c", remove_cmd(manager), "_", *remove],
                as_user=False,
                timeout=timeout,
                max_output_bytes=4_194_304,
            )
        return list(config.packages)

    def ensure(
        self,
        yaml_arg: str | Path | None,
        *,
        force: bool = False,
        recreate: bool = False,
        lock_timeout: float = 30.0,
    ) -> OperationResult:
        config = self.validate(yaml_arg)
        backend = self._backend_for(config)
        if not backend.runtime_present():
            raise RuntimeError(
                f"{backend.name} backend is not installed or not in PATH"
            )
        store = ProjectStateStore(config.file_path)
        operation_id = uuid.uuid4().hex
        session_path = store.begin_session(operation_id, "ensure")
        actions_done: list[str] = []
        warnings: list[str] = []
        backup: str | None = None
        backup_was_running = False
        created = False
        try:
            with store.lock(timeout=lock_timeout):
                plan = self._plan_config(config)
                kinds = {action.kind for action in plan.actions}
                if recreate and backend.container_exists(config.name):
                    kinds.add("recreate")
                if (
                    "recreate" in kinds or any(a.destructive for a in plan.actions)
                ) and not force:
                    raise PermissionError(
                        "Plan contains destructive actions; rerun with --force"
                    )
                _, record = self._record(config)
                applied = record.applied_packages if record else []
                needs_create = "create" in kinds or "recreate" in kinds
                if needs_create:
                    if backend.container_exists(config.name):
                        if backend.label(config.name, "io.zaribox.managed") != "true":
                            raise PermissionError(
                                f"Refusing to replace unmanaged container '{config.name}'"
                            )
                        owner = backend.label(config.name, "io.zaribox.project-id")
                        if owner is not None and owner != store.project_id:
                            raise PermissionError(
                                f"Container '{config.name}' belongs to another ZariBox project"
                            )
                        backup = f"{config.name}.backup-{operation_id[:8]}"
                        backup_was_running = backend.is_running(config.name)
                        try:
                            backend.stop(config.name)
                        except RuntimeError:
                            pass
                        backend.rename(config.name, backup)
                    home_dir = config.home_dir or _default_home(config.name)
                    home = Path(home_dir)
                    home_existed = home.exists()
                    home.mkdir(parents=True, exist_ok=True)
                    if not home_existed:
                        home.chmod(0o700)
                    backend.create(
                        config.name,
                        config.image,
                        home_dir,
                        config.extra_flags,
                        config.home_mount,
                        policy=self._create_policy(config),
                    )
                    created = True
                    actions_done.append("create" if backup is None else "recreate")
                    applied = []
                applied = self._sync_packages(backend, config, applied)
                if config.packages:
                    actions_done.append("sync_packages")
                if needs_create:
                    for command in config.run:
                        backend.exec(
                            config.name,
                            ["sh", "-lc", command],
                            as_user=True,
                            timeout=self._provision_timeout(),
                            max_output_bytes=4_194_304,
                        )
                    if config.run:
                        actions_done.append("run_post_install")
                    backend.post_install(
                        config.name, config.home_dir or _default_home(config.name)
                    )
                ttl = _ttl_seconds(config.metadata.ttl if config.metadata else None)
                previous_created = record.created_at if record else None
                new_record = ProjectRecord(
                    project_id=store.project_id,
                    config_path=str(config.file_path.resolve()),
                    container_name=config.name,
                    backend=resolve_backend(config),
                    applied_identity_digest=container_identity_hash(config),
                    applied_packages=applied,
                    image=config.image,
                    image_digest=backend.image_digest(config.name),
                    security_profile=self._profile(config),
                    created_at=previous_created
                    or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    expires_at=time.time() + ttl if ttl is not None else None,
                )
                store.save(new_record)
                try:
                    legacy = StateStore(config.name)
                    legacy.save_yaml_path(config.name, config.file_path.resolve())
                    legacy.save_container_hash(
                        config.name, new_record.applied_identity_digest
                    )
                    legacy.save_packages(config.name, applied)
                except OSError as exc:
                    warnings.append(f"Legacy state update failed: {exc}")
                if backup is not None:
                    try:
                        backend.rm(backup)
                    except RuntimeError as exc:
                        warnings.append(f"Backup cleanup failed: {exc}")
            return OperationResult(
                operation_id,
                bool(actions_done),
                config.name,
                tuple(actions_done),
                tuple(warnings),
            )
        except Exception:
            if created and backend.container_exists(config.name):
                try:
                    backend.rm(config.name)
                except RuntimeError:
                    pass
            if backup is not None and backend.container_exists(backup):
                try:
                    backend.rename(backup, config.name)
                    if backup_was_running:
                        backend.start(config.name)
                except RuntimeError:
                    pass
            raise
        finally:
            session_path.unlink(missing_ok=True)

    def execute(
        self,
        target: str | Path,
        argv: Sequence[str],
        *,
        timeout: float = 300.0,
        max_output_bytes: int = 1_048_576,
        as_root: bool = False,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        if not argv:
            raise ValueError("At least one command argument is required")
        config = self.config_for_target(target)
        backend = self._backend_for(config)
        if not backend.container_exists(config.name):
            raise RuntimeError(
                f"Container '{config.name}' does not exist; run ensure first"
            )
        store = ProjectStateStore(config.file_path)
        self._assert_owned(backend, config, store)
        with store.lock():
            result: CommandResult = backend.exec(
                config.name,
                list(argv),
                as_user=not as_root,
                check=False,
                timeout=timeout,
                max_output_bytes=max_output_bytes,
                agent_mode=self._profile(config) == "agent",
                workdir=workdir or config.workdir,
                env=env,
            )
        return ExecResult(
            config.name,
            tuple(argv),
            result.returncode,
            result.stdout,
            result.stderr,
            result.timed_out,
            result.truncated,
        )

    def destroy(
        self, target: str | Path, *, force: bool = False, lock_timeout: float = 30.0
    ) -> OperationResult:
        if not force:
            raise PermissionError("Destroy requires explicit confirmation/force")
        config = self.config_for_target(target)
        backend = self._backend_for(config)
        store = ProjectStateStore(config.file_path)
        operation_id = uuid.uuid4().hex
        changed = False
        with store.lock(timeout=lock_timeout):
            if backend.container_exists(config.name):
                if backend.label(config.name, "io.zaribox.managed") != "true":
                    raise PermissionError(
                        f"Refusing to destroy unmanaged container '{config.name}'"
                    )
                owner = backend.label(config.name, "io.zaribox.project-id")
                if owner is not None and owner != store.project_id:
                    raise PermissionError(
                        f"Container '{config.name}' belongs to another ZariBox project"
                    )
                try:
                    backend.stop(config.name)
                except RuntimeError:
                    pass
                backend.rm(config.name)
                changed = True
            store.clear()
            StateStore(config.name).clear_cache(config.name)
        return OperationResult(
            operation_id, changed, config.name, ("destroy",) if changed else ()
        )

    def export_packages(self, target: str | Path) -> list[str]:
        from .commands.export import _fetch_installed_packages, _merge_into_config

        config = self.config_for_target(target)
        backend = self._backend_for(config)
        if not backend.container_exists(config.name):
            raise RuntimeError(f"Container '{config.name}' does not exist")
        store = ProjectStateStore(config.file_path)
        self._assert_owned(backend, config, store)
        with store.lock():
            packages = _fetch_installed_packages(backend, config.name, config.image)
            added = _merge_into_config(config.file_path, config, packages)
            if added:
                record = store.load()
                if record is not None:
                    record.applied_packages = sorted(set(config.packages) | set(added))
                    store.save(record)
                    StateStore(config.name).save_packages(
                        config.name, record.applied_packages
                    )
            return added

    def list_boxes(self) -> list[dict[str, object]]:
        import json

        root = ProjectStateStore(Path.cwd() / "placeholder.yaml").root / "projects"
        results: list[dict[str, object]] = []
        if not root.is_dir():
            return results
        for state_path in sorted(root.glob("*/state.json")):
            try:
                raw = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                    continue
                name = str(raw["container_name"])
                backend = self._backend or make_backend(
                    str(raw.get("backend", "podman"))
                )
                exists = backend.container_exists(name)
                results.append(
                    {
                        "project_id": raw.get("project_id"),
                        "name": name,
                        "config_path": raw.get("config_path"),
                        "exists": exists,
                        "running": backend.is_running(name) if exists else False,
                        "image": raw.get("image"),
                        "image_digest": raw.get("image_digest"),
                        "security_profile": raw.get("security_profile", "default"),
                        "expires_at": raw.get("expires_at"),
                    }
                )
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return results

    def cleanup(self) -> list[str]:
        removed_sessions = cleanup_expired_sessions()
        root = ProjectStateStore(Path.cwd() / "placeholder.yaml").root / "projects"
        if not root.is_dir():
            return removed_sessions
        now = time.time()
        for state_path in sorted(root.glob("*/state.json")):
            try:
                import json

                raw = json.loads(state_path.read_text(encoding="utf-8"))
                expires_at = raw.get("expires_at")
                config_path = Path(str(raw.get("config_path", "")))
                if expires_at is None or float(expires_at) > now:
                    continue
                if config_path.is_file():
                    result = self.destroy(config_path, force=True, lock_timeout=0.1)
                    if result.changed:
                        removed_sessions.append(result.container)
                    continue

                name = str(raw.get("container_name", ""))
                project = str(raw.get("project_id", ""))
                backend = self._backend or make_backend(
                    str(raw.get("backend", "podman"))
                )
                store = ProjectStateStore(config_path)
                with store.lock(timeout=0.1):
                    if backend.container_exists(name):
                        if backend.label(name, "io.zaribox.managed") != "true":
                            continue
                        if backend.label(name, "io.zaribox.project-id") != project:
                            continue
                        try:
                            backend.stop(name)
                        except RuntimeError:
                            pass
                        backend.rm(name)
                        removed_sessions.append(name)
                    store.clear()
                    StateStore(name).clear_cache(name)
            except (OSError, ValueError, TypeError, RuntimeError, TimeoutError):
                continue
        return removed_sessions
