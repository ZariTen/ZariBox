from __future__ import annotations

from .base import Backend
from .podman import PodmanBackend


def make_backend(name: str) -> Backend:
    if name == "podman":
        return PodmanBackend()
    raise ValueError(f"Unsupported backend: {name}")
