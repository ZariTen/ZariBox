from __future__ import annotations

_PKG_MANAGERS: dict[str, tuple[str, str, list[str]]] = {
    "pacman": (
        'pacman -Syu --noconfirm "$@"',
        'pacman -Rns --noconfirm "$@"',
        ["pacman", "-Qen"],
    ),
    "apt": (
        'apt-get install -y "$@"',
        'apt-get remove -y "$@"',
        ["apt-mark", "showmanual"],
    ),
    "dnf": (
        'dnf install -y "$@"',
        'dnf remove -y "$@"',
        ["dnf", "repoquery", "--userinstalled"],
    ),
    "zypper": (
        'zypper install -y "$@"',
        'zypper remove -y "$@"',
        ["zypper", "packages", "--userinstalled"],
    ),
    "apk": ('apk add "$@"', 'apk del "$@"', ["apk", "info", "-q"]),
    "xbps": ('xbps-install -y "$@"', 'xbps-remove -y "$@"', ["xbps-query", "-m"]),
}

_PREFIX_MAP: list[tuple[tuple[str, ...], str]] = [
    (("arch", "manjaro", "endeavour"), "pacman"),
    (("ubuntu", "debian", "pop", "mint"), "apt"),
    (("fedora", "centos", "rhel"), "dnf"),
    (("opensuse", "suse"), "zypper"),
    (("alpine",), "apk"),
    (("void",), "xbps"),
]

_PROBE_ORDER = ("pacman", "apt", "dnf", "zypper", "apk", "xbps")
_PROBE_BINARIES = {
    "pacman": "pacman",
    "apt": "apt-get",
    "dnf": "dnf",
    "zypper": "zypper",
    "apk": "apk",
    "xbps": "xbps-install",
}


def detect_pkgmgr(image: str) -> str:
    name = image.lower().split("/")[-1].split(":")[0]
    for prefixes, mgr in _PREFIX_MAP:
        if name.startswith(prefixes):
            return mgr
    return "auto"


def probe_cmd() -> list[str]:
    """Return a command that prints the first supported manager in a container."""
    checks = " ".join(
        f"if command -v {_PROBE_BINARIES[mgr]} >/dev/null 2>&1; "
        f"then echo {mgr}; exit 0; fi;"
        for mgr in _PROBE_ORDER
    )
    return ["sh", "-c", f"{checks} exit 1"]


def _manager(mgr: str) -> tuple[str, str, list[str]]:
    try:
        return _PKG_MANAGERS[mgr]
    except KeyError as exc:
        raise ValueError(f"Unsupported package manager: {mgr}") from exc


def _auto_script(operation: int) -> str:
    branches = []
    for mgr in _PROBE_ORDER:
        command = _PKG_MANAGERS[mgr][operation]
        branches.append(
            f"if command -v {_PROBE_BINARIES[mgr]} >/dev/null 2>&1; "
            f"then exec {command}; fi"
        )
    return (
        "; ".join(branches)
        + "; echo 'No supported package manager found' >&2; exit 127"
    )


def install_cmd(mgr: str) -> str:
    return _auto_script(0) if mgr == "auto" else _manager(mgr)[0]


def remove_cmd(mgr: str) -> str:
    return _auto_script(1) if mgr == "auto" else _manager(mgr)[1]


def list_cmd(mgr: str) -> list[str]:
    if mgr != "auto":
        return _manager(mgr)[2]
    branches = []
    for candidate in _PROBE_ORDER:
        command = " ".join(_PKG_MANAGERS[candidate][2])
        branches.append(
            f"if command -v {_PROBE_BINARIES[candidate]} >/dev/null 2>&1; "
            f"then exec {command}; fi"
        )
    script = (
        "; ".join(branches)
        + "; echo 'No supported package manager found' >&2; exit 127"
    )
    return ["sh", "-c", script]
