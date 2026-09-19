from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - ZariBox currently targets Linux
    fcntl = None  # type: ignore[assignment]


def _state_root() -> Path:
    configured = os.environ.get("ZARIBOX_STATE_HOME")
    if configured:
        return Path(configured).expanduser()
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = (
        Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local" / "state"
    )
    return base / "zaribox"


def project_id(config_path: Path) -> str:
    canonical = str(config_path.expanduser().resolve())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, contents: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.chmod(mode)
        os.replace(temp_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        temp_path.unlink(missing_ok=True)


@dataclass(slots=True)
class ProjectRecord:
    project_id: str
    config_path: str
    container_name: str
    backend: str
    applied_identity_digest: str
    applied_packages: list[str] = field(default_factory=list)
    image: str = ""
    image_digest: str | None = None
    security_profile: str = "default"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    expires_at: float | None = None
    schema_version: int = 1

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ProjectRecord:
        if value.get("schema_version") != 1:
            raise ValueError("Unsupported project state schema")
        packages_value = value.get("applied_packages", [])
        if not isinstance(packages_value, list):
            raise TypeError("applied_packages must be a list")
        expires_value = value.get("expires_at")
        if expires_value is not None and not isinstance(expires_value, (int, float)):
            raise ValueError("expires_at must be numeric")
        return cls(
            project_id=str(value["project_id"]),
            config_path=str(value["config_path"]),
            container_name=str(value["container_name"]),
            backend=str(value["backend"]),
            applied_identity_digest=str(value["applied_identity_digest"]),
            applied_packages=[str(item) for item in packages_value],
            image=str(value.get("image", "")),
            image_digest=(
                str(value["image_digest"]) if value.get("image_digest") else None
            ),
            security_profile=str(value.get("security_profile", "default")),
            created_at=str(value.get("created_at", utc_now())),
            updated_at=str(value.get("updated_at", utc_now())),
            expires_at=float(expires_value) if expires_value is not None else None,
        )


@dataclass(slots=True)
class SessionRecord:
    operation_id: str
    project_id: str
    kind: str
    phase: str
    temporary_resources: list[str]
    started_at: float
    expires_at: float
    schema_version: int = 1


class ProjectStateStore:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path.expanduser().resolve()
        self.project_id = project_id(self.config_path)
        self.root = _state_root()
        self.path = self.root / "projects" / self.project_id / "state.json"
        self.lock_path = self.root / "locks" / f"{self.project_id}.lock"

    def load(self) -> ProjectRecord | None:
        if not self.path.is_file():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid project state: {self.path}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(  # noqa: TRY004 - public state errors are operational
                f"Invalid project state: {self.path}"
            )
        try:
            return ProjectRecord.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid project state: {self.path}") from exc

    def save(self, record: ProjectRecord) -> None:
        record.updated_at = utc_now()
        atomic_write(
            self.path, json.dumps(asdict(record), indent=2, sort_keys=True) + "\n"
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
        except OSError:
            pass

    @contextmanager
    def lock(self, *, timeout: float = 30.0) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as stream:
            if fcntl is None:
                yield
                return
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out waiting for project lock: {self.project_id}"
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def begin_session(
        self, operation_id: str, kind: str, ttl_seconds: int = 3600
    ) -> Path:
        now = time.time()
        record = SessionRecord(
            operation_id, self.project_id, kind, "started", [], now, now + ttl_seconds
        )
        path = self.root / "sessions" / f"{operation_id}.json"
        atomic_write(path, json.dumps(asdict(record), indent=2, sort_keys=True) + "\n")
        return path


def cleanup_expired_sessions(*, now: float | None = None) -> list[str]:
    current = time.time() if now is None else now
    sessions = _state_root() / "sessions"
    removed: list[str] = []
    if not sessions.is_dir():
        return removed
    for path in sorted(sessions.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                continue
            if float(raw["expires_at"]) > current:
                continue
            # Session cleanup is deliberately conservative: only records are removed.
            # Runtime resources are reconciled by ensure/destroy after ownership checks.
            path.unlink()
            removed.append(str(raw.get("operation_id", path.stem)))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return removed
