from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ZariConfig:
    file_path: Path
    name: str
    image: str
    backend: str | None = None
    home_dir: str | None = None
    extra_flags: str = ""
    packages: list[str] = field(default_factory=list)
    run: list[str] = field(default_factory=list)
