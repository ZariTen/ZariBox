backend_podman_selinux_enabled() {
	if command -v getenforce >/dev/null 2>&1; then
		[[ "$(getenforce 2>/dev/null || echo Disabled)" != "Disabled" ]]
		return
	fi

	[[ -f /sys/fs/selinux/enforce ]]
}

backend_podman_mount_opts() {
	local opts="$1"
	if backend_podman_selinux_enabled && [[ "${ZARIBOX_PODMAN_RELABEL:-0}" == "1" ]]; then
		opts+=",z"
	fi
	echo "$opts"
}

backend_podman_is_rootless() {
	[[ "$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || echo false)" == "true" ]]
}

backend_podman_container_home() {
	local name="$1"
	podman inspect --format '{{ index .Config.Labels "io.zaribox.home" }}' "$name" 2>/dev/null
}

backend_podman_ensure_user() {
	local name="$1"
	local home_dir="$2"
	local host_uid host_gid host_user
	host_uid="$(id -u)"
	host_gid="$(id -g)"
	host_user="$(id -un)"

	podman start "$name" >/dev/null 2>&1 || true
	podman exec \
		--user 0 \
		--env "ZB_USER=${host_user}" \
		--env "ZB_UID=${host_uid}" \
		--env "ZB_GID=${host_gid}" \
		--env "ZB_HOME=${home_dir}" \
		"$name" sh -lc '
			set -eu

			if ! getent group "$ZB_GID" >/dev/null 2>&1; then
				if command -v groupadd >/dev/null 2>&1; then
					groupadd -g "$ZB_GID" "$ZB_USER" 2>/dev/null || true
				elif command -v addgroup >/dev/null 2>&1; then
					addgroup -g "$ZB_GID" "$ZB_USER" 2>/dev/null || true
				fi
			fi

			if ! getent passwd "$ZB_UID" >/dev/null 2>&1; then
				if command -v useradd >/dev/null 2>&1; then
					useradd -m -d "$ZB_HOME" -u "$ZB_UID" -g "$ZB_GID" -s /bin/bash "$ZB_USER" 2>/dev/null || \
					useradd -m -d "$ZB_HOME" -u "$ZB_UID" -g "$ZB_GID" "$ZB_USER" 2>/dev/null || true
				elif command -v adduser >/dev/null 2>&1; then
					adduser -D -h "$ZB_HOME" -u "$ZB_UID" -G "$ZB_USER" "$ZB_USER" 2>/dev/null || true
				fi
			fi

			if command -v sudo >/dev/null 2>&1 && getent passwd "$ZB_UID" >/dev/null 2>&1; then
				mkdir -p /etc/sudoers.d
				if [[ -f /etc/sudoers ]] && ! grep -Eq "^[[:space:]]*#includedir[[:space:]]+/etc/sudoers\.d" /etc/sudoers; then
					printf "\n#includedir /etc/sudoers.d\n" >> /etc/sudoers
				fi
				printf "%s ALL=(ALL:ALL) NOPASSWD:ALL\n" "$ZB_USER" > /etc/sudoers.d/90-zaribox-user
				printf "#%s ALL=(ALL:ALL) NOPASSWD:ALL\n" "$ZB_UID" >> /etc/sudoers.d/90-zaribox-user
				chmod 0440 /etc/sudoers.d/90-zaribox-user || true
			fi

			chown -R "$ZB_UID:$ZB_GID" "$ZB_HOME" 2>/dev/null || true
		'
}

backend_podman_container_exists() {
	podman container inspect "$1" >/dev/null 2>&1
}

backend_podman_create() {
	local name="$1"
	local image="$2"
	local home_dir="$3"
	local extra_flags="${4:-}"
	local host_uid host_gid host_user
	local mnt_rw_rslave mnt_ro mnt_home mnt_ro_norelabel
	host_uid="$(id -u)"
	host_gid="$(id -g)"
	host_user="$(id -un)"
	mnt_rw_rslave="$(backend_podman_mount_opts "rw,rslave")"
	mnt_ro="$(backend_podman_mount_opts "ro")"
	mnt_home="$(backend_podman_mount_opts "rslave")"
	mnt_ro_norelabel="ro"

	local create_args=(
		podman create
		--name "$name"
		--hostname "$name"
		--label "io.zaribox.managed=true"
		--label "io.zaribox.home=$home_dir"
		--security-opt label=disable
		--network host
		--ipc host
		--env "HOME=$home_dir"
		--env "USER=$host_user"
		--env "LOGNAME=$host_user"
		--workdir "$home_dir"
		--volume "$home_dir:$home_dir:${mnt_home}"
	)

	if backend_podman_is_rootless; then
		create_args+=(--userns keep-id)
	fi

	if [[ -n "${TERM:-}" ]]; then
		create_args+=(--env "TERM=$TERM")
	fi

	if [[ -n "${DISPLAY:-}" ]]; then
		create_args+=(--env "DISPLAY=$DISPLAY")
		[[ -d /tmp/.X11-unix ]] && create_args+=(--volume "/tmp/.X11-unix:/tmp/.X11-unix:${mnt_rw_rslave}")

		if [[ -n "${XAUTHORITY:-}" && -f "${XAUTHORITY}" ]]; then
			create_args+=(--env "XAUTHORITY=${XAUTHORITY}" --volume "${XAUTHORITY}:${XAUTHORITY}:${mnt_ro}")
		elif [[ -f "${HOME}/.Xauthority" ]]; then
			create_args+=(--env "XAUTHORITY=${HOME}/.Xauthority" --volume "${HOME}/.Xauthority:${HOME}/.Xauthority:${mnt_ro}")
		fi
	fi

	if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" ]]; then
		create_args+=(
			--env "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
			--volume "$XDG_RUNTIME_DIR:$XDG_RUNTIME_DIR:${mnt_rw_rslave}"
		)

		if [[ -n "${WAYLAND_DISPLAY:-}" && -S "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ]]; then
			create_args+=(--env "WAYLAND_DISPLAY=$WAYLAND_DISPLAY")
		fi

		if [[ -S "${XDG_RUNTIME_DIR}/bus" ]]; then
			create_args+=(--env "DBUS_SESSION_BUS_ADDRESS=unix:path=${XDG_RUNTIME_DIR}/bus")
		elif [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
			create_args+=(--env "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS")
		fi

		if [[ -d "${XDG_RUNTIME_DIR}/pulse" ]]; then
			create_args+=(--volume "${XDG_RUNTIME_DIR}/pulse:${XDG_RUNTIME_DIR}/pulse:${mnt_rw_rslave}")
			if [[ -n "${PULSE_SERVER:-}" ]]; then
				create_args+=(--env "PULSE_SERVER=$PULSE_SERVER")
			elif [[ -S "${XDG_RUNTIME_DIR}/pulse/native" ]]; then
				create_args+=(--env "PULSE_SERVER=unix:${XDG_RUNTIME_DIR}/pulse/native")
			fi
		fi
	fi

	[[ -e /dev/dri ]] && create_args+=(--device /dev/dri)
	[[ -e /dev/kfd ]] && create_args+=(--device /dev/kfd)

	if [[ -e /etc/localtime ]]; then
		create_args+=(--volume "/etc/localtime:/etc/localtime:${mnt_ro_norelabel}")
	fi

	if [[ -n "$extra_flags" ]]; then
		IFS=" " read -r -a eflags <<< "$extra_flags"
		create_args+=("${eflags[@]}")
	fi

	create_args+=("$image" "sleep" "infinity")

	"${create_args[@]}"
	podman start "$name" >/dev/null
	backend_podman_ensure_user "$name" "$home_dir"
}

backend_podman_exec() {
	local name="$1"
	shift

	podman start "$name" >/dev/null 2>&1 || true
	podman exec --user 0 "$name" "$@"
}

backend_podman_exec_user() {
	local name="$1"
	shift
	local host_uid host_gid host_user
	host_uid="$(id -u)"
	host_gid="$(id -g)"
	host_user="$(id -un)"

	podman start "$name" >/dev/null 2>&1 || true
	podman exec --user "${host_uid}:${host_gid}" --env "USER=${host_user}" --env "LOGNAME=${host_user}" "$name" "$@"
}

backend_podman_fix_home_permissions() {
	local name="$1"
	local home_dir="$2"
	local host_uid host_gid
	host_uid="$(id -u)"
	host_gid="$(id -g)"

	podman start "$name" >/dev/null 2>&1 || true
	backend_podman_ensure_user "$name" "$home_dir"
	podman exec "$name" sh -lc "chown -R ${host_uid}:${host_gid} \"${home_dir}\""
}

backend_podman_enter() {
	local name="$1"
	local preferred_shell="${SHELL##*/}"
	local host_uid host_gid host_user home_dir
	host_uid="$(id -u)"
	host_gid="$(id -g)"
	host_user="$(id -un)"
	home_dir="$(backend_podman_container_home "$name")"
	[[ -z "$home_dir" ]] && home_dir="${HOME}"

	podman start "$name" >/dev/null 2>&1 || true
	backend_podman_ensure_user "$name" "$home_dir"
	exec podman exec -it --user "${host_uid}:${host_gid}" --env "USER=${host_user}" --env "LOGNAME=${host_user}" --env "HOME=${home_dir}" "$name" sh -lc "if command -v ${preferred_shell@Q} >/dev/null 2>&1; then exec ${preferred_shell@Q} -l; elif command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh -l; fi"
}

backend_podman_stop() {
	local name="$1"
	podman stop "$name" >/dev/null
}

backend_podman_rm() {
	local name="$1"
	podman rm -f "$name" >/dev/null
}

backend_podman_ps() {
	podman ps -a
}
