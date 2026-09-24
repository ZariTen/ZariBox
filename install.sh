#!/usr/bin/env bash
set -euo pipefail

usage() {
	cat <<'USAGE'
Usage:
	./install.sh install
	./install.sh uninstall
Builds the release binaries with cargo when available, otherwise with
`nix build`, and installs zaribox and zaribox-mcp into ~/.local/bin.
USAGE
}

target_bin_dir() {
	echo "$HOME/.local/bin"
}

build_binaries() {
	if command -v cargo >/dev/null 2>&1; then
		cargo build --locked --release
		built_dir="$root_dir/target/release"
	elif command -v nix >/dev/null 2>&1; then
		nix build "$root_dir#zaribox" --out-link "$root_dir/result"
		built_dir="$root_dir/result/bin"
	else
		echo "Neither cargo nor nix was found; cannot build zaribox." >&2
		exit 1
	fi
}

install_program() {
	local bin_dir name
	bin_dir="$(target_bin_dir)"
	mkdir -p "$bin_dir"
	for name in zaribox zaribox-mcp; do
		install -m 0755 "$built_dir/$name" "$bin_dir/$name"
		echo "Installed $bin_dir/$name"
	done
	# Clean up files left by the former Python installer.
	rm -rf "$HOME/.local/lib/zaribox"
}

remove_program() {
	local bin_dir name
	bin_dir="$(target_bin_dir)"
	for name in zaribox zaribox-mcp; do
		if [[ -f "$bin_dir/$name" ]]; then
			rm -f "$bin_dir/$name"
			echo "Removed $bin_dir/$name"
		fi
	done
	rm -rf "$HOME/.local/lib/zaribox"
}

if [[ $# -lt 1 ]]; then
	usage
	exit 1
fi

action="$1"
shift

if [[ $# -gt 0 ]]; then
	case "$1" in
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
fi

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root_dir"

case "$action" in
	install)
		build_binaries
		install_program
		echo "Installed. Ensure ~/.local/bin is in PATH."
		if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
			echo "Warning: $HOME/.local/bin is not in your PATH. Add it to your shell profile to use zaribox."
		fi
		;;
	uninstall)
		remove_program
		echo "Uninstalled zaribox."
		;;
	-h|--help)
		usage
		;;
	*)
		echo "Unknown action: $action" >&2
		usage
		exit 1
		;;
esac
