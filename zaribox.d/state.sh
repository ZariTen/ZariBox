container_hash_path() { echo "${CACHE_DIR}/${1}.container.hash"; }
packages_cache_path() { echo "${CACHE_DIR}/${1}.packages"; }

saved_container_hash() {
    local p
    p=$(container_hash_path "$1")
    [[ -f "$p" ]] && cat "$p" || echo ""
}

save_container_hash() {
    echo "$2" > "$(container_hash_path "$1")"
}

saved_packages() {
    local p
    p=$(packages_cache_path "$1")
    [[ -f "$p" ]] && cat "$p" || echo ""
}

save_packages() {
    local name="$1"
    shift
    if [[ $# -gt 0 ]]; then
        printf '%s\n' "$@" | sort > "$(packages_cache_path "$name")"
    else
        > "$(packages_cache_path "$name")"
    fi
}

container_identity_hash() {
    local yaml="$1"
    local image home_dir extra_flags
    image=$(parse_yaml "$yaml" "Image")
    home_dir=$(parse_yaml "$yaml" "HomeDir")
    extra_flags=$(parse_yaml "$yaml" "ExtraFlags")
    printf '%s\n%s\n%s\n' "$image" "$home_dir" "$extra_flags" | sha256sum | awk '{print $1}'
}

container_exists() {
    backend_container_exists "$1"
}