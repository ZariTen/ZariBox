backend_resolve_name() {
    local yaml="${1:-}"
    local selected=""

    if [[ -n "${ZARIBOX_BACKEND:-}" ]]; then
        selected="${ZARIBOX_BACKEND}"
    elif [[ -n "$yaml" ]]; then
        selected=$(parse_yaml "$yaml" "Backend")
    fi

    [[ -z "$selected" ]] && selected="distrobox"

    case "$selected" in
        distrobox)
            echo "distrobox"
            ;;
        podman)
            echo "podman"
            ;;
        *)
            err "Unsupported backend: '$selected'. Supported: distrobox, podman"
            exit 1
            ;;
    esac
}

backend_use() {
    local yaml="${1:-}"
    ACTIVE_BACKEND=$(backend_resolve_name "$yaml")
}

backend_require_selected() {
    if [[ -z "${ACTIVE_BACKEND:-}" ]]; then
        backend_use ""
    fi
}

backend_detect() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox)
            command -v distrobox >/dev/null 2>&1
            ;;
        podman)
            command -v podman >/dev/null 2>&1
            ;;
        *)
            return 1
            ;;
    esac
}

backend_container_exists() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_container_exists "$@" ;;
        podman) backend_podman_container_exists "$@" ;;
        *) return 1 ;;
    esac
}

backend_create() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_create "$@" ;;
        podman) backend_podman_create "$@" ;;
        *) return 1 ;;
    esac
}

backend_exec() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_exec "$@" ;;
        podman) backend_podman_exec "$@" ;;
        *) return 1 ;;
    esac
}

backend_exec_user() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_exec "$@" ;;
        podman) backend_podman_exec_user "$@" ;;
        *) return 1 ;;
    esac
}

backend_fix_home_permissions() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) : ;;
        podman) backend_podman_fix_home_permissions "$@" ;;
        *) return 1 ;;
    esac
}

backend_enter() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_enter "$@" ;;
        podman) backend_podman_enter "$@" ;;
        *) return 1 ;;
    esac
}

backend_stop() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_stop "$@" ;;
        podman) backend_podman_stop "$@" ;;
        *) return 1 ;;
    esac
}

backend_rm() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_rm "$@" ;;
        podman) backend_podman_rm "$@" ;;
        *) return 1 ;;
    esac
}

backend_ps() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_ps "$@" ;;
        podman) backend_podman_ps "$@" ;;
        *) return 1 ;;
    esac
}
