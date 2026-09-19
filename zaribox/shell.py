from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


def command_exists(binary: str) -> bool:
    return shutil.which(binary) is not None


def run_command(
    args: Sequence[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    timeout: float | None = None,
    max_output_bytes: int | None = None,
) -> CommandResult:
    command = list(args)
    if timeout is not None and timeout < 0:
        raise ValueError("timeout must be non-negative")
    if max_output_bytes is not None and max_output_bytes < 0:
        raise ValueError("max_output_bytes must be non-negative")

    if not capture_output or (timeout is None and max_output_bytes is None):
        try:
            if timeout is None:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=capture_output,
                    text=True,
                )
            else:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=capture_output,
                    text=True,
                    timeout=timeout,
                )
            result = CommandResult(
                args=command,
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                args=command,
                returncode=124,
                stdout=_timeout_text(exc.stdout),
                stderr=_timeout_text(exc.stderr),
                timed_out=True,
            )
    else:
        result = _run_bounded(command, timeout, max_output_bytes)

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr
        )
    return result


def _timeout_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _run_bounded(
    command: list[str], timeout: float | None, max_output_bytes: int | None
) -> CommandResult:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    outputs = [bytearray(), bytearray()]
    remaining = [max_output_bytes]
    truncated = [False]
    lock = threading.Lock()

    def drain(stream: BinaryIO, destination: bytearray) -> None:
        while chunk := stream.read(65536):
            with lock:
                if remaining[0] is None:
                    destination.extend(chunk)
                elif remaining[0] > 0:
                    kept = chunk[: remaining[0]]
                    destination.extend(kept)
                    remaining[0] -= len(kept)
                    truncated[0] = truncated[0] or len(kept) != len(chunk)
                else:
                    truncated[0] = True

    assert process.stdout is not None
    assert process.stderr is not None
    threads = [
        threading.Thread(target=drain, args=(process.stdout, outputs[0]), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, outputs[1]), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        returncode = process.wait()
    for thread in threads:
        thread.join()

    return CommandResult(
        args=command,
        returncode=124 if timed_out else returncode,
        stdout=outputs[0].decode(errors="replace"),
        stderr=outputs[1].decode(errors="replace"),
        timed_out=timed_out,
        truncated=truncated[0],
    )
