from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def volume_args(source: str | Path, target: str | Path, options: str) -> list[str]:
    return ["--volume", f"{source}:{target}:{options}"]


def env_args(env: Mapping[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in env.items():
        args.extend(["--env", f"{key}={value}"])
    return args


def identity_env_args(user: str, home_dir: str) -> list[str]:
    return env_args({"USER": user, "LOGNAME": user, "HOME": home_dir})


def user_exec_args(uid: int, gid: int, user: str, home_dir: str) -> list[str]:
    return ["--user", f"{uid}:{gid}", *identity_env_args(user, home_dir)]
