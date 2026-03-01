from __future__ import annotations

import sys
from typing import TextIO

RED = "\033[0;31m"
GRN = "\033[0;32m"
YLW = "\033[0;33m"
BLU = "\033[0;34m"
MAG = "\033[0;35m"
CYN = "\033[0;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RST = "\033[0m"


def _print(message: str, *, stream: TextIO = sys.stdout) -> None:
    print(message, file=stream)


def log(message: str) -> None:
    _print(f"{BLU}{BOLD}[zaribox]{RST} {message}")


def ok(message: str) -> None:
    _print(f"{GRN}{BOLD}  ok{RST} {message}")


def warn(message: str) -> None:
    _print(f"{YLW}{BOLD}  warn{RST} {message}")


def err(message: str) -> None:
    _print(f"{RED}{BOLD}  error{RST} {message}", stream=sys.stderr)


def step(message: str) -> None:
    _print(f"{MAG}  ->{RST} {message}")
