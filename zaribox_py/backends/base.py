from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ..shell import CommandResult


class Backend(ABC):
    name: str

    @abstractmethod
    def runtime_present(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def container_exists(self, name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create(
        self,
        name: str,
        image: str,
        home_dir: str,
        extra_flags: str = "",
        graphics_types: Sequence[str] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def exec(
        self,
        name: str,
        command: Sequence[str],
        *,
        as_user: bool = False,
        check: bool = True,
        capture_output: bool = True,
    ) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def enter(self, name: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def stop(self, name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def rm(self, name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def ps(self) -> str:
        raise NotImplementedError
