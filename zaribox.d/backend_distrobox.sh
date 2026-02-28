backend_distrobox_container_exists() {
    distrobox list 2>/dev/null | grep -q "^| $1 " || \
    distrobox list 2>/dev/null | grep -qw "$1"
}

backend_distrobox_create() {
    local name="$1"
    local image="$2"
    local home_dir="$3"
    local extra_flags="${4:-}"

    local create_args=(distrobox create --name "$name" --image "$image" --home "$home_dir")
    if [[ -n "$extra_flags" ]]; then
        IFS=" " read -r -a eflags <<< "$extra_flags"
        create_args+=("${eflags[@]}")
    fi

    "${create_args[@]}"
}

backend_distrobox_exec() {
    local name="$1"
    shift
    distrobox enter "$name" -- "$@"
}

backend_distrobox_enter() {
    local name="$1"
    exec distrobox enter "$name"
}

backend_distrobox_stop() {
    local name="$1"
    distrobox stop "$name" --yes
}

backend_distrobox_rm() {
    local name="$1"
    distrobox rm "$name" --force
}

backend_distrobox_ps() {
    distrobox list
}
