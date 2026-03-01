from __future__ import annotations

from .base import Backend
from .distrobox import DistroboxBackend
from .podman import PodmanBackend


def make_backend(name: str) -> Backend:
    if name == "distrobox":
        return DistroboxBackend()
    if name == "podman":
        return PodmanBackend()
    raise ValueError(f"Unsupported backend: {name}")
