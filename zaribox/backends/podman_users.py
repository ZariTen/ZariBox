from __future__ import annotations

import shlex


def machine_id_setup_script() -> str:
    return """
        if [ ! -s /etc/machine-id ]; then
            if command -v systemd-machine-id-setup >/dev/null 2>&1; then
                systemd-machine-id-setup >/dev/null 2>&1 || true
            fi
            if [ ! -s /etc/machine-id ] && [ -r /proc/sys/kernel/random/uuid ]; then
                tr -d '-' < /proc/sys/kernel/random/uuid > /etc/machine-id 2>/dev/null || true
            fi
        fi
        """


def user_setup_script(
    *,
    uid: int,
    gid: int,
    user: str,
    home_dir: str,
    allow_passwordless_sudo: bool,
) -> str:
    quoted_user = shlex.quote(user)
    sudo_setup = ""
    if allow_passwordless_sudo:
        sudo_setup = f"""
        mkdir -p /etc/sudoers.d
        printf '%s ALL=(ALL:ALL) NOPASSWD:ALL\\n' {quoted_user} > /etc/sudoers.d/90-zaribox-user
        chmod 0440 /etc/sudoers.d/90-zaribox-user
        """

    return f"""
        getent group {gid} >/dev/null 2>&1 ||
            groupadd -g {gid} {quoted_user} 2>/dev/null ||
            addgroup -g {gid} {quoted_user}
        getent passwd {uid} >/dev/null 2>&1 ||
            useradd -M -d {shlex.quote(home_dir)} -u {uid} -g {gid} {quoted_user} 2>/dev/null ||
            adduser -H -h {shlex.quote(home_dir)} -u {uid} -G {quoted_user} -D {quoted_user}
        {sudo_setup}
        """
