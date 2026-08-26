from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path


def mount_options(options: str) -> str:
    """Add the optional SELinux relabel flag to a Podman mount."""
    if os.environ.get("ZARIBOX_PODMAN_RELABEL") == "1":
        return f"{options},z"
    return options


def parse_mounts(output: str) -> list[tuple[Path, Path]]:
    """Parse host-to-container mount paths from Podman inspect output."""
    mounts: list[tuple[Path, Path]] = []
    for line in output.splitlines():
        try:
            source_text, destination_text = line.split("\t", 1)
        except ValueError:
            continue

        source = Path(source_text).expanduser()
        destination = Path(destination_text).expanduser()
        if source.is_absolute() and destination.is_absolute():
            mounts.append((source, destination))
    return mounts


def mounted_workdir(host_dir: Path, mounts: Sequence[tuple[Path, Path]]) -> str | None:
    """Translate a host directory through a bind mount, if one exists."""
    try:
        resolved_host_dir = host_dir.resolve()
    except (OSError, RuntimeError):
        return None

    matches: list[tuple[int, Path]] = []
    for source, destination in mounts:
        try:
            resolved_source = source.resolve()
            relative = resolved_host_dir.relative_to(resolved_source)
        except (OSError, RuntimeError, ValueError):
            continue

        container_dir = destination
        if relative != Path("."):
            container_dir /= relative
        matches.append((len(resolved_source.parts), container_dir))

    if not matches:
        return None
    return str(max(matches, key=lambda match: match[0])[1])
