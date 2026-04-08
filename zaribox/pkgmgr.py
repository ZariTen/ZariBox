from __future__ import annotations

_PKG_MANAGERS: dict[str, tuple[str, str]] = {
    "pacman": ('pacman -Syu --noconfirm "$@"', 'pacman -Rns --noconfirm "$@"'),
    "apt": ('apt-get install -y "$@"', 'apt-get remove -y "$@"'),
    "dnf": ('dnf install -y "$@"', 'dnf remove -y "$@"'),
    "zypper": ('zypper install -y "$@"', 'zypper remove -y "$@"'),
    "apk": ('apk add "$@"', 'apk del "$@"'),
    "xbps": ('xbps-install -y "$@"', 'xbps-remove -y "$@"'),
}

_PREFIX_MAP: list[tuple[tuple[str, ...], str]] = [
    (("arch", "manjaro", "endeavour"), "pacman"),
    (("ubuntu", "debian", "pop", "mint"), "apt"),
    (("fedora", "centos", "rhel"), "dnf"),
    (("opensuse", "suse"), "zypper"),
    (("alpine",), "apk"),
    (("void",), "xbps"),
]

_DEFAULT = "apt"


def detect_pkgmgr(image: str) -> str:
    name = image.lower().split("/")[-1].split(":")[0]
    for prefixes, mgr in _PREFIX_MAP:
        if name.startswith(prefixes):
            return mgr
    return "auto"


def install_cmd(mgr: str) -> str:
    return _PKG_MANAGERS.get(mgr, _PKG_MANAGERS[_DEFAULT])[0]


def remove_cmd(mgr: str) -> str:
    return _PKG_MANAGERS.get(mgr, _PKG_MANAGERS[_DEFAULT])[1]
