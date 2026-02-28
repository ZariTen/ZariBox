#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROG_NAME="zaribox"
LIB_NAME="zaribox"

usage() {
    cat <<'EOF'
Usage:
  ./install.sh install [--prefix PATH] [--user]
  ./install.sh uninstall [--prefix PATH] [--user]

Options:
  --prefix PATH   Install root prefix (default: /usr/local)
  --user          Install into ~/.local (equivalent to --prefix "$HOME/.local")

Examples:
  ./install.sh install
  ./install.sh install --user
  ./install.sh uninstall --prefix /usr/local
EOF
}

err() {
    printf '%s\n' "error: $*" >&2
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        err "required command not found: $1"
        exit 1
    }
}

main() {
    local action="${1:-}"
    shift || true

    [[ -z "$action" ]] && {
        usage
        exit 1
    }

    local prefix="/usr/local"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --prefix)
                shift
                [[ $# -gt 0 ]] || {
                    err "--prefix requires a value"
                    exit 1
                }
                prefix="$1"
                ;;
            --user)
                prefix="${HOME}/.local"
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                err "unknown option: $1"
                usage
                exit 1
                ;;
        esac
        shift
    done

    local bin_dir="${prefix}/bin"
    local lib_dir="${prefix}/lib/${LIB_NAME}"
    local launcher="${bin_dir}/${PROG_NAME}"
    local lib_entry="${lib_dir}/${PROG_NAME}"

    need_cmd install
    need_cmd cp
    need_cmd rm

    case "$action" in
        install)
            if [[ ! -f "${SCRIPT_DIR}/zaribox" || ! -d "${SCRIPT_DIR}/zaribox.d" ]]; then
                err "run this script from the project root (where 'zaribox' and 'zaribox.d' exist)"
                exit 1
            fi

            install -d "$bin_dir" "$lib_dir"
            install -m 0755 "${SCRIPT_DIR}/zaribox" "$lib_entry"

            rm -rf "${lib_dir}/zaribox.d"
            cp -a "${SCRIPT_DIR}/zaribox.d" "$lib_dir/"

            cat > "$launcher" <<EOF
#!/usr/bin/env bash
exec "${lib_entry}" "\$@"
EOF
            chmod 0755 "$launcher"

            printf '%s\n' "Installed ${PROG_NAME}"
            printf '  bin: %s\n' "$launcher"
            printf '  lib: %s\n' "$lib_dir"
            ;;
        uninstall)
            rm -f "$launcher"
            rm -rf "$lib_dir"
            printf '%s\n' "Uninstalled ${PROG_NAME} from prefix: ${prefix}"
            ;;
        *)
            err "unknown action: $action"
            usage
            exit 1
            ;;
    esac
}

main "$@"