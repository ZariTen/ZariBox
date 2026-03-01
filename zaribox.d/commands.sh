require_backend_runtime() {
    if ! backend_detect; then
        err "${ACTIVE_BACKEND} backend is not installed or not in PATH."
        exit 1
    fi
}

cmd_apply() {
    local yaml
    yaml=$(resolve_yaml "${1:-}")
    backend_use "$yaml"
    require_backend_runtime
    local name
    name=$(container_name_from "$yaml")
    local image
    image=$(parse_yaml "$yaml" "Image")
    local home_dir
    home_dir=$(parse_yaml "$yaml" "HomeDir")
    [[ -z "$home_dir" ]] && home_dir="${HOME}/.local/share/zaribox/homes/${name}"
    local extra_flags
    extra_flags=$(parse_yaml "$yaml" "ExtraFlags")

    if [[ "${ACTIVE_BACKEND}" == "podman" ]]; then
        local graphics_types=()
        mapfile -t graphics_types < <(parse_yaml_object_list_value "$yaml" "Graphics" "type")

        local has_nvidia=false
        local graphics_type
        for graphics_type in "${graphics_types[@]}"; do
            if [[ "${graphics_type,,}" == "nvidia" ]]; then
                has_nvidia=true
                break
            fi
        done

        if [[ "$has_nvidia" == true ]] && [[ "$extra_flags" != *"nvidia.com/gpu=all"* ]]; then
            extra_flags+="${extra_flags:+ }--device nvidia.com/gpu=all"
        fi
    fi

    if [[ -z "$image" ]]; then
        err "Image field is required in $yaml"
        exit 1
    fi

    local current_id_hash
    current_id_hash=$(container_identity_hash "$yaml")
    local old_id_hash
    old_id_hash=$(saved_container_hash "$name")

    local desired_packages=()
    mapfile -t desired_packages < <(parse_yaml_list "$yaml" "Packages")

    printf '\n'
    printf '%s\n' "${BOLD}${CYN}ZariBox apply${RST}  ${DIM}v${VERSION}${RST}"
    printf '%s\n' "  ${DIM}config  ${RST}${yaml}"
    printf '%s\n' "  ${DIM}name    ${RST}${name}"
    printf '%s\n' "  ${DIM}image   ${RST}${image}"
    printf '\n'

    local needs_recreate=false
    local container_is_new=false

    if ! container_exists "$name"; then
        container_is_new=true
        needs_recreate=true
    elif [[ "$current_id_hash" != "$old_id_hash" ]]; then
        warn "Container config changed (Image/HomeDir/ExtraFlags/Graphics) -- recreating '${name}'..."
        needs_recreate=true
    fi

    if [[ "$needs_recreate" == true ]]; then
        if [[ "$container_is_new" == false ]]; then
            step "Stopping and removing old container..."
            backend_stop "$name" 2>/dev/null || true
            backend_rm "$name" 2>/dev/null || true
            ok "Old container removed"
        else
            step "Creating new container '${name}'..."
        fi

        mkdir -p "$home_dir"

        step "Pulling image and creating container..."
        backend_create "$name" "$image" "$home_dir" "$extra_flags"
        ok "Container created"
        save_container_hash "$name" "$current_id_hash"

        save_packages "$name"

        if [[ ${#desired_packages[@]} -gt 0 ]]; then
            local mgr
            mgr=$(detect_pkgmgr "$image")
            local icmd
            icmd=$(install_cmd "$mgr")
            step "Installing ${#desired_packages[@]} package(s) via ${mgr}..."
            backend_exec "$name" true 2>/dev/null || true
            backend_exec "$name" bash -c "$icmd" _ "${desired_packages[@]}"
            ok "Packages installed: ${desired_packages[*]}"
            save_packages "$name" "${desired_packages[@]}"
        else
            warn "No packages listed in $yaml"
        fi
    else
        ok "Container '${name}' is up to date -- checking packages..."

        local old_pkgs_sorted new_pkgs_sorted
        old_pkgs_sorted=$(saved_packages "$name" | sort)
        new_pkgs_sorted=$(printf '%s\n' "${desired_packages[@]}" | sort)

        local to_install=()
        local to_remove=()

        while IFS= read -r pkg; do
            [[ -n "$pkg" ]] && to_install+=("$pkg")
        done < <(comm -23 <(echo "$new_pkgs_sorted") <(echo "$old_pkgs_sorted"))

        while IFS= read -r pkg; do
            [[ -n "$pkg" ]] && to_remove+=("$pkg")
        done < <(comm -23 <(echo "$old_pkgs_sorted") <(echo "$new_pkgs_sorted"))

        if [[ ${#to_install[@]} -eq 0 && ${#to_remove[@]} -eq 0 ]]; then
            ok "Packages are in sync -- nothing to do."
            printf '\n'
            return 0
        fi

        local mgr
        mgr=$(detect_pkgmgr "$image")

        if [[ ${#to_install[@]} -gt 0 ]]; then
            local icmd
            icmd=$(install_cmd "$mgr")
            step "Installing ${#to_install[@]} new package(s): ${to_install[*]}"
            backend_exec "$name" bash -c "$icmd" _ "${to_install[@]}"
            ok "Installed: ${to_install[*]}"
        fi

        if [[ ${#to_remove[@]} -gt 0 ]]; then
            local rcmd
            rcmd=$(remove_cmd "$mgr")
            step "Removing ${#to_remove[@]} package(s): ${to_remove[*]}"
            backend_exec "$name" bash -c "$rcmd" _ "${to_remove[@]}"
            ok "Removed: ${to_remove[*]}"
        fi

        save_packages "$name" "${desired_packages[@]}"
    fi

    if [[ "$needs_recreate" == true ]]; then
        local run_cmds=()
        mapfile -t run_cmds < <(parse_yaml_list "$yaml" "Run")
        if [[ ${#run_cmds[@]} -gt 0 ]]; then
            step "Normalizing home directory ownership for user commands..."
            backend_fix_home_permissions "$name" "$home_dir"
            step "Running post-install commands..."
            for cmd_line in "${run_cmds[@]}"; do
                step "  $ $cmd_line"
                backend_exec_user "$name" bash -c "$cmd_line"
            done
            ok "Post-install commands done"
        fi
    fi

    printf '\n'
    ok "${BOLD}Done.${RST} Container '${name}' is ready."
    printf '%s\n' "  ${DIM}Run:${RST}  zaribox enter ${yaml}"
    printf '\n'
}

cmd_enter() {
    local yaml
    yaml=$(resolve_yaml "${1:-}")
    backend_use "$yaml"
    require_backend_runtime
    local name
    name=$(container_name_from "$yaml")

    if ! container_exists "$name"; then
        warn "Container '$name' does not exist. Running apply first..."
        cmd_apply "$yaml"
    fi

    log "Entering '${name}'..."
    backend_enter "$name"
}

cmd_destroy() {
    local yaml
    yaml=$(resolve_yaml "${1:-}")
    backend_use "$yaml"
    require_backend_runtime
    local name
    name=$(container_name_from "$yaml")

    if ! container_exists "$name"; then
        warn "Container '${name}' does not exist."
        return 0
    fi

    printf '%s\n' "${RED}${BOLD}This will destroy container '${name}' (home dir is preserved).${RST}"
    read -r -p "  Confirm? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || {
        log "Aborted."
        return 0
    }

    backend_stop "$name" 2>/dev/null || true
    backend_rm "$name"
    rm -f "$(container_hash_path "$name")" "$(packages_cache_path "$name")"
    ok "Container '${name}' destroyed. Home dir preserved."
}

cmd_status() {
    local yaml
    yaml=$(resolve_yaml "${1:-}")
    backend_use "$yaml"
    require_backend_runtime
    local name
    name=$(container_name_from "$yaml")

    local current_id_hash
    current_id_hash=$(container_identity_hash "$yaml")
    local old_id_hash
    old_id_hash=$(saved_container_hash "$name")

    local desired_packages=()
    mapfile -t desired_packages < <(parse_yaml_list "$yaml" "Packages")
    local old_pkgs_sorted new_pkgs_sorted
    old_pkgs_sorted=$(saved_packages "$name" | sort)
    new_pkgs_sorted=$(printf '%s\n' "${desired_packages[@]}" | sort)

    printf '\n'
    printf '%s\n' "${BOLD}${CYN}ZariBox Status${RST}"
    printf '%s\n' "  config   : ${yaml}"
    printf '%s\n' "  name     : ${name}"
    printf '%s\n' "  image    : $(parse_yaml "$yaml" "Image")"

    if container_exists "$name"; then
        printf '%s\n' "  exists   : ${GRN}yes${RST}"
    else
        printf '%s\n' "  exists   : ${RED}no${RST}"
    fi

    if [[ "$current_id_hash" == "$old_id_hash" ]]; then
        printf '%s\n' "  container: ${GRN}in sync${RST}"
    else
        printf '%s\n' "  container: ${YLW}changed -- will recreate on apply${RST}"
    fi

    local to_install=()
    local to_remove=()
    while IFS= read -r pkg; do
        [[ -n "$pkg" ]] && to_install+=("$pkg")
    done < <(comm -23 <(echo "$new_pkgs_sorted") <(echo "$old_pkgs_sorted"))
    while IFS= read -r pkg; do
        [[ -n "$pkg" ]] && to_remove+=("$pkg")
    done < <(comm -23 <(echo "$old_pkgs_sorted") <(echo "$new_pkgs_sorted"))

    if [[ ${#to_install[@]} -eq 0 && ${#to_remove[@]} -eq 0 ]]; then
        printf '%s\n' "  packages : ${GRN}in sync${RST}"
    else
        printf '%s\n' "  packages : ${YLW}drift detected${RST}"
        for p in "${to_install[@]}"; do
            printf '%s\n' "    ${GRN}+ ${p}${RST}  ${DIM}(to install)${RST}"
        done
        for p in "${to_remove[@]}"; do
            printf '%s\n' "    ${RED}- ${p}${RST}  ${DIM}(to remove)${RST}"
        done
    fi

    printf '%s\n' "  packages :"
    parse_yaml_list "$yaml" "Packages" | while read -r pkg; do
        printf '%s\n' "    ${DIM}-${RST} ${pkg}"
    done
    printf '\n'
}

cmd_list() {
    backend_use ""
    require_backend_runtime
    printf '\n'
    printf '%s\n' "${BOLD}${CYN}ZariBox containers${RST}  ${DIM}(from ${CACHE_DIR})${RST}"
    printf '\n'
    for f in "${CACHE_DIR}"/*.container.hash; do
        [[ -f "$f" ]] || continue
        local cname
        cname=$(basename "$f" .container.hash)
        if container_exists "$cname"; then
            printf '%s\n' "  ${GRN}+${RST}  ${cname}"
        else
            printf '%s\n' "  ${RED}-${RST}  ${cname}  ${DIM}(not running)${RST}"
        fi
    done
    printf '\n'
}

cmd_help() {
    cat <<EOF

${BOLD}${CYN}ZariBox${RST} v${VERSION}  -- Declarative container manager

${BOLD}Usage:${RST}
  zaribox <command> [file.yaml]

${BOLD}Commands:${RST}
  ${GRN}apply${RST}    [file.yaml]   Sync container to match config
  ${GRN}enter${RST}    [file.yaml]   Enter the container (applies first if needed)
  ${GRN}destroy${RST}  [file.yaml]   Remove the container (home dir preserved)
  ${GRN}status${RST}   [file.yaml]   Show sync status with package drift
  ${GRN}list${RST}                   List all ZariBox-managed containers
  ${GRN}help${RST}                   Show this help

${BOLD}Config file format (YAML):${RST}

  ${DIM}# archbox.yaml${RST}
  Name: archbox          ${DIM}# optional, defaults to filename${RST}
  Image: archlinux
  HomeDir: ~/.zariboxes/arch   ${DIM}# optional${RST}
    Backend: distrobox     ${DIM}# optional: distrobox|podman (default: distrobox)${RST}

  Packages:
  - base-devel
  - git
  - neovim
  - ripgrep

  Run:                   ${DIM}# optional post-install commands (only run on full recreate)${RST}
  - curl -sfL https://get.example.com | sh

    ExtraFlags: --nvidia   ${DIM}# optional extra backend create flags${RST}

${BOLD}Examples:${RST}
  zaribox apply archbox.yaml
  zaribox enter archbox.yaml
  zaribox enter                ${DIM}# auto-detect .yaml in cwd${RST}

EOF
}

main() {
    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        apply)   cmd_apply "$@" ;;
        enter)   cmd_enter "$@" ;;
        destroy) cmd_destroy "$@" ;;
        status)  cmd_status "$@" ;;
        list)    cmd_list ;;
        help|-h|--help) cmd_help ;;
        *)
            err "Unknown command: $cmd"
            cmd_help
            exit 1
            ;;
    esac
}