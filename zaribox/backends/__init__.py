from __future__ import annotations

from .podman import PodmanBackend

__all__ = ["PodmanBackend", "make_backend"]


def make_backend(name: str) -> PodmanBackend:
    if name == "podman":
        return PodmanBackend()
    raise ValueError(f"Unsupported backend: {name}")
