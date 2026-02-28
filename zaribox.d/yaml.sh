parse_yaml() {
    local file="$1"
    local key="$2"

    grep -m1 "^${key}:" "$file" 2>/dev/null | sed 's/^[^:]*:[[:space:]]*//' | tr -d '"' | tr -d "'" | sed 's/[[:space:]]*#.*//' || true
}

parse_yaml_list() {
    local file="$1"
    local key="$2"

    awk -v key="$key" '
        /^[A-Za-z]/ { in_block = 0 }
        $0 ~ "^"key":" { in_block = 1; next }
        in_block && /^[[:space:]]*-[[:space:]]/ {
            sub(/^[[:space:]]*-[[:space:]]*/, "")
            sub(/[[:space:]]*#.*$/, "")
            gsub(/["\047]/, "")
            if (length($0) > 0) print
        }
    ' "$file"
}