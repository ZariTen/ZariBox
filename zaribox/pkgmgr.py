from __future__ import annotations

INSTALL_COMMANDS: dict[str, str] = {
    "pacman": 'pacman -Syu --noconfirm "$@"',
    "apt": 'apt-get install -y "$@"',
    "dnf": 'dnf install -y "$@"',
    "zypper": 'zypper install -y "$@"',
    "apk": 'apk add "$@"',
    "xbps": 'xbps-install -y "$@"',
}

REMOVE_COMMANDS: dict[str, str] = {
    "pacman": 'pacman -Rns --noconfirm "$@"',
    "apt": 'apt-get remove -y "$@"',
    "dnf": 'dnf remove -y "$@"',
    "zypper": 'zypper remove -y "$@"',
    "apk": 'apk del "$@"',
    "xbps": 'xbps-remove -y "$@"',
}


def detect_pkgmgr(image: str) -> str:
    image_name = image.lower().split("/")[-1].split(":")[0]
    if image_name.startswith(("arch", "manjaro", "endeavour")):
        return "pacman"
    if image_name.startswith(("ubuntu", "debian", "pop", "mint")):
        return "apt"
    if image_name.startswith(("fedora", "centos", "rhel")):
        return "dnf"
    if image_name.startswith(("opensuse", "suse")):
        return "zypper"
    if image_name.startswith("alpine"):
        return "apk"
    if image_name.startswith("void"):
        return "xbps"
    return "auto"


def install_cmd(mgr: str) -> str:
    return INSTALL_COMMANDS.get(mgr, INSTALL_COMMANDS["apt"])


def remove_cmd(mgr: str) -> str:
    return REMOVE_COMMANDS.get(mgr, REMOVE_COMMANDS["apt"])
