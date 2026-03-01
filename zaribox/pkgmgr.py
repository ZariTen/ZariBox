from __future__ import annotations


def detect_pkgmgr(image: str) -> str:
    image_name = image.lower()
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
    if mgr == "pacman":
        return 'if command -v sudo >/dev/null 2>&1; then sudo pacman -Syu --noconfirm "$@"; else pacman -Syu --noconfirm "$@"; fi'
    if mgr == "apt":
        return 'if command -v sudo >/dev/null 2>&1; then sudo apt-get install -y "$@"; else apt-get install -y "$@"; fi'
    if mgr == "dnf":
        return 'if command -v sudo >/dev/null 2>&1; then sudo dnf install -y "$@"; else dnf install -y "$@"; fi'
    if mgr == "zypper":
        return 'if command -v sudo >/dev/null 2>&1; then sudo zypper install -y "$@"; else zypper install -y "$@"; fi'
    if mgr == "apk":
        return 'if command -v sudo >/dev/null 2>&1; then sudo apk add "$@"; else apk add "$@"; fi'
    if mgr == "xbps":
        return 'if command -v sudo >/dev/null 2>&1; then sudo xbps-install -y "$@"; else xbps-install -y "$@"; fi'
    return 'if command -v sudo >/dev/null 2>&1; then sudo apt-get install -y "$@"; else apt-get install -y "$@"; fi'


def remove_cmd(mgr: str) -> str:
    if mgr == "pacman":
        return 'if command -v sudo >/dev/null 2>&1; then sudo pacman -Rns --noconfirm "$@"; else pacman -Rns --noconfirm "$@"; fi'
    if mgr == "apt":
        return 'if command -v sudo >/dev/null 2>&1; then sudo apt-get remove -y "$@"; else apt-get remove -y "$@"; fi'
    if mgr == "dnf":
        return 'if command -v sudo >/dev/null 2>&1; then sudo dnf remove -y "$@"; else dnf remove -y "$@"; fi'
    if mgr == "zypper":
        return 'if command -v sudo >/dev/null 2>&1; then sudo zypper remove -y "$@"; else zypper remove -y "$@"; fi'
    if mgr == "apk":
        return 'if command -v sudo >/dev/null 2>&1; then sudo apk del "$@"; else apk del "$@"; fi'
    if mgr == "xbps":
        return 'if command -v sudo >/dev/null 2>&1; then sudo xbps-remove -y "$@"; else xbps-remove -y "$@"; fi'
    return 'if command -v sudo >/dev/null 2>&1; then sudo apt-get remove -y "$@"; else apt-get remove -y "$@"; fi'
