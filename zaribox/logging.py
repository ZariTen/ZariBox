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

verbose: bool = True


def _colored(stream: TextIO) -> bool:
    return hasattr(stream, "isatty") and stream.isatty()


def _fmt(color: str, label: str, message: str, stream: TextIO) -> str:
    if _colored(stream):
        return f"{color}{BOLD}{label}{RST} {message}"
    return f"{label} {message}"


def _print(message: str, *, stream: TextIO = sys.stdout) -> None:
    if verbose:
        print(message, file=stream)


def log(message: str) -> None:
    _print(_fmt(BLU, "[zaribox]", message, sys.stdout))


def ok(message: str) -> None:
    _print(_fmt(GRN, "  ok", message, sys.stdout))


def warn(message: str) -> None:
    _print(_fmt(YLW, "  warn", message, sys.stdout))


def err(message: str) -> None:
    _print(_fmt(RED, "  error", message, sys.stderr), stream=sys.stderr)


def step(message: str) -> None:
    _print(_fmt(MAG, "  ->", message, sys.stdout))
