from __future__ import annotations

import os


def mount_options(options: str) -> str:
    """Add the optional SELinux relabel flag to a Podman mount."""
    if os.environ.get("ZARIBOX_PODMAN_RELABEL") == "1":
        return f"{options},z"
    return options
