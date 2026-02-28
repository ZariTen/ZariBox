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
        pacman)  echo "sudo pacman -Syu --noconfirm" ;;
        apt)     echo "sudo apt-get install -y" ;;
        dnf)     echo "sudo dnf install -y" ;;
        zypper)  echo "sudo zypper install -y" ;;
        apk)     echo "sudo apk add" ;;
        xbps)    echo "sudo xbps-install -y" ;;
        *)       echo "sudo apt-get install -y" ;;
    esac
}

remove_cmd() {
    local mgr="$1"
    case "$mgr" in
        pacman)  echo "sudo pacman -Rns --noconfirm" ;;
        apt)     echo "sudo apt-get remove -y" ;;
        dnf)     echo "sudo dnf remove -y" ;;
        zypper)  echo "sudo zypper remove -y" ;;
        apk)     echo "sudo apk del" ;;
        xbps)    echo "sudo xbps-remove -y" ;;
        *)       echo "sudo apt-get remove -y" ;;
    esac
}