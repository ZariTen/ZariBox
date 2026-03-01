#!/usr/bin/env bash
set -euo pipefail

usage() {
	cat <<'EOF'
Usage:
	./install.sh install [--python <exe>]
	./install.sh uninstall

Options:
	--python <exe>   Python executable to use for launcher (default: python3)

Examples:
	./install.sh install
	./install.sh install --python python3
  ./install.sh uninstall
EOF
}

target_bin_dir() {
	echo "$HOME/.local/bin"
}

target_lib_dir() {
	echo "$HOME/.local/lib/zaribox"
}

install_program() {
	local lib_dir
	lib_dir="$(target_lib_dir)"

	rm -rf "$lib_dir"
	mkdir -p "$lib_dir"

	cp -a "$root_dir/zaribox/." "$lib_dir/"

	echo "Installed program files to $lib_dir"
}

install_launcher() {
	local bin_dir launcher python_path lib_dir
	bin_dir="$(target_bin_dir)"
	launcher="$bin_dir/zaribox"
	python_path="$(command -v "$python_exe")"
	lib_dir="$(target_lib_dir)"

	mkdir -p "$bin_dir"
	cat >"$launcher" <<EOF
#!/usr/bin/env bash
set -euo pipefail
	exec "$python_path" "$lib_dir/__main__.py" "\$@"
EOF
	chmod +x "$launcher"
	echo "Installed launcher at $launcher"
}

remove_launcher() {
	local launcher
	launcher="$(target_bin_dir)/zaribox"
	if [[ -f "$launcher" ]]; then
		rm -f "$launcher"
		echo "Removed launcher at $launcher"
	fi
}

remove_program() {
	local lib_dir
	lib_dir="$(target_lib_dir)"
	if [[ -d "$lib_dir" ]]; then
		rm -rf "$lib_dir"
		echo "Removed program files at $lib_dir"
	fi
}

if [[ $# -lt 1 ]]; then
	usage
	exit 1
fi

action="$1"
shift

python_exe="python3"

while [[ $# -gt 0 ]]; do
	case "$1" in
		--python)
			if [[ $# -lt 2 ]]; then
				echo "Missing value for --python" >&2
				exit 1
			fi
			python_exe="$2"
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "Unknown argument: $1" >&2
			usage
			exit 1
			;;
	esac
done

if ! command -v "$python_exe" >/dev/null 2>&1; then
	echo "Python executable not found: $python_exe" >&2
	exit 1
fi

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root_dir"

case "$action" in
	install)
		install_program
		install_launcher
		echo "Installed. Ensure ~/.local/bin is in PATH."
		;;
	uninstall)
		remove_launcher
		remove_program
		echo "Uninstalled zaribox."
		;;
	*)
		echo "Unknown action: $action" >&2
		usage
		exit 1
		;;
esac
