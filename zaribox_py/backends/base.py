from __future__ import annotations

from abc import ABC, abstractmethod


class Backend(ABC):
    name: str

    @abstractmethod
    def runtime_present(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def container_exists(self, name: str) -> bool:
        raise NotImplementedError
