detect_pkgmgr() {
    local image="$1"
    case "$image" in
        arch*|manjaro*|endeavour*)  echo "pacman" ;;
        ubuntu*|debian*|pop*|mint*) echo "apt" ;;
        fedora*|centos*|rhel*)      echo "dnf" ;;
        opensuse*|suse*)            echo "zypper" ;;
        alpine*)                    echo "apk" ;;
        void*)                      echo "xbps" ;;
        *)                          echo "auto" ;;
    esac
}

install_cmd() {
    local mgr="$1"
    case "$mgr" in
        pacman)  echo 'if command -v sudo >/dev/null 2>&1; then sudo pacman -Syu --noconfirm "$@"; else pacman -Syu --noconfirm "$@"; fi' ;;
        apt)     echo 'if command -v sudo >/dev/null 2>&1; then sudo apt-get install -y "$@"; else apt-get install -y "$@"; fi' ;;
        dnf)     echo 'if command -v sudo >/dev/null 2>&1; then sudo dnf install -y "$@"; else dnf install -y "$@"; fi' ;;
        zypper)  echo 'if command -v sudo >/dev/null 2>&1; then sudo zypper install -y "$@"; else zypper install -y "$@"; fi' ;;
        apk)     echo 'if command -v sudo >/dev/null 2>&1; then sudo apk add "$@"; else apk add "$@"; fi' ;;
        xbps)    echo 'if command -v sudo >/dev/null 2>&1; then sudo xbps-install -y "$@"; else xbps-install -y "$@"; fi' ;;
        *)       echo 'if command -v sudo >/dev/null 2>&1; then sudo apt-get install -y "$@"; else apt-get install -y "$@"; fi' ;;
    esac
}

remove_cmd() {
    local mgr="$1"
    case "$mgr" in
        pacman)  echo 'if command -v sudo >/dev/null 2>&1; then sudo pacman -Rns --noconfirm "$@"; else pacman -Rns --noconfirm "$@"; fi' ;;
        apt)     echo 'if command -v sudo >/dev/null 2>&1; then sudo apt-get remove -y "$@"; else apt-get remove -y "$@"; fi' ;;
        dnf)     echo 'if command -v sudo >/dev/null 2>&1; then sudo dnf remove -y "$@"; else dnf remove -y "$@"; fi' ;;
        zypper)  echo 'if command -v sudo >/dev/null 2>&1; then sudo zypper remove -y "$@"; else zypper remove -y "$@"; fi' ;;
        apk)     echo 'if command -v sudo >/dev/null 2>&1; then sudo apk del "$@"; else apk del "$@"; fi' ;;
        xbps)    echo 'if command -v sudo >/dev/null 2>&1; then sudo xbps-remove -y "$@"; else xbps-remove -y "$@"; fi' ;;
        *)       echo 'if command -v sudo >/dev/null 2>&1; then sudo apt-get remove -y "$@"; else apt-get remove -y "$@"; fi' ;;
    esac
}