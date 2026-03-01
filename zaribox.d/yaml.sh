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

parse_yaml_object_list_value() {
    local file="$1"
    local list_key="$2"
    local field_key="$3"

    awk -v list_key="$list_key" -v field_key="$field_key" '
        /^[A-Za-z]/ { in_block = 0 }
        $0 ~ "^"list_key":[[:space:]]*$" { in_block = 1; next }

        in_block {
            if ($0 ~ /^[[:space:]]*-[[:space:]]/) {
                line = $0
                sub(/^[[:space:]]*-[[:space:]]*/, "", line)
                if (line ~ "^"field_key":[[:space:]]*") {
                    sub("^"field_key":[[:space:]]*", "", line)
                    sub(/[[:space:]]*#.*$/, "", line)
                    gsub(/["\047]/, "", line)
                    if (length(line) > 0) print line
                }
                next
            }

            if ($0 ~ "^[[:space:]]*"field_key":[[:space:]]*") {
                line = $0
                sub("^[[:space:]]*"field_key":[[:space:]]*", "", line)
                sub(/[[:space:]]*#.*$/, "", line)
                gsub(/["\047]/, "", line)
                if (length(line) > 0) print line
            }
        }
    ' "$file"
}