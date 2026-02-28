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
        *)
            err "Unsupported backend: '$selected'. Supported: distrobox"
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
        *)
            return 1
            ;;
    esac
}

backend_container_exists() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_container_exists "$@" ;;
        *) return 1 ;;
    esac
}

backend_create() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_create "$@" ;;
        *) return 1 ;;
    esac
}

backend_exec() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_exec "$@" ;;
        *) return 1 ;;
    esac
}

backend_enter() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_enter "$@" ;;
        *) return 1 ;;
    esac
}

backend_stop() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_stop "$@" ;;
        *) return 1 ;;
    esac
}

backend_rm() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_rm "$@" ;;
        *) return 1 ;;
    esac
}

backend_ps() {
    backend_require_selected
    case "$ACTIVE_BACKEND" in
        distrobox) backend_distrobox_ps "$@" ;;
        *) return 1 ;;
    esac
}
