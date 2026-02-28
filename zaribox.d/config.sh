resolve_yaml() {
    local arg="${1:-}"
    if [[ -n "$arg" && -f "$arg" ]]; then
        echo "$arg"
    elif [[ -n "$arg" && -f "${arg}.yaml" ]]; then
        echo "${arg}.yaml"
    elif [[ -n "$arg" && -f "${arg}.yml" ]]; then
        echo "${arg}.yml"
    else
        local found
        found=$(find . -maxdepth 1 \( -name "*.yaml" -o -name "*.yml" \) | head -1)
        if [[ -z "$found" ]]; then
            err "No .yaml file found. Pass one explicitly or run from a directory containing one."
            exit 1
        fi
        echo "$found"
    fi
}

container_name_from() {
    local yaml="$1"
    local name
    name=$(parse_yaml "$yaml" "Name")
    if [[ -z "$name" ]]; then
        name=$(basename "$yaml" | sed -E 's/\.ya?ml$//')
    fi
    echo "$name"
}