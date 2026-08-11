# ZariBox

![ZariBox](zaribox.svg)

ZariBox is a declarative container manager for reproducible dev boxes. You describe a container in a YAML file (base image, packages, post-install setup), and ZariBox creates it and keeps it in sync with that description.

It works with both [distrobox](https://github.com/89luca89/distrobox) and [podman](https://podman.io/) as backends.

## Quick start

`devbox.yaml`:

```yaml
Name: devbox
Image: archlinux
Packages:
  - git
  - neovim
  - fish
Run:
  - echo 'exec fish' >> ~/.bashrc
```

```bash
zaribox create devbox.yaml   # create the container
zaribox enter devbox         # open a shell inside it
zaribox status devbox        # check for package drift
```

Run `zaribox` with no arguments (or `zaribox help`) to see the available commands.

## Backend selection

The backend is resolved in this order:

1. `ZARIBOX_BACKEND` environment variable
2. `Backend:` field in the YAML file
3. default: `distrobox`

Supported backends: `distrobox` and `podman`. The chosen backend must be installed and in `PATH`.

## Commands

All commands except `create` take a container name: ZariBox remembers which YAML file each container was created from (stored under `~/.config/zaribox`).

| Command | Description |
| --- | --- |
| `zaribox create [file.yaml]` | Create a container from a YAML config. If the container already exists it is recreated (home dir is preserved). With no file argument, the only `*.yaml`/`*.yml` in the current directory is used; if there are several, you must pass one explicitly. |
| `zaribox apply <name>` | Sync the container to match the config: install packages that are missing, remove ones that were dropped from `Packages:`. |
| `zaribox status <name>` | Show sync status: whether the container exists, whether the config changed since the last sync, and package drift in both directions. |
| `zaribox export <name>` | Fetch the packages you installed by hand inside the container and merge them into the `Packages:` block of the YAML file. |
| `zaribox enter <name>` | Open a shell inside the container. |
| `zaribox list` | List all ZariBox-managed containers, marking which ones are currently running. |
| `zaribox remove <name>` | Destroy the container after a confirmation prompt. The home directory is preserved; ZariBox's state for it is cleared. |

## YAML reference

```yaml
Name: devbox          # optional, defaults to the file name
Image: archlinux      # required
Backend: podman       # optional, see "Backend selection"
HomeDir: /home/$USER/Documents/devbox   # optional
ExtraFlags: --device nvidia.com/gpu=all # optional, extra flags for container creation
Packages:             # optional
  - git
  - neovim
Run:                  # optional, run as your user after install
  - echo 'exec fish' >> ~/.bashrc
```

| Field | Description |
| --- | --- |
| `Name` | Container name. Defaults to the YAML file name without extension. |
| `Image` | Base image. Short names are expanded to their full `docker.io` reference with a `:latest` tag. |
| `Backend` | `distrobox` or `podman`. |
| `HomeDir` | Home directory for the container; environment variables like `$USER` are expanded. Defaults to `$XDG_DATA_HOME/zaribox/home/<name>` (usually `~/.local/share/zaribox/home/<name>`). Persists across recreations. |
| `ExtraFlags` | Extra flags passed through to `distrobox create` / `podman create`. |
| `Packages` | Packages to install when the container is created or synced. |
| `Run` | Shell commands executed as your user inside the container after package install. |

The package manager is auto-detected from the image name: `arch`/`manjaro`/`endeavour` → pacman, `ubuntu`/`debian`/`pop`/`mint` → apt, `fedora`/`centos`/`rhel` → dnf, `opensuse`/`suse` → zypper, `alpine` → apk, `void` → xbps. Unknown images fall back to apt. `export` reads the distro's list of *explicitly installed* packages, so packages pulled in as dependencies are not added to your config.

## Install

**Requirements:** Python 3.10+, PyYAML, and `distrobox` or `podman` in your `PATH`.

```bash
# Nix
nix run github:ZariTen/ZariBox

# pip
pip install git+https://github.com/ZariTen/ZariBox.git

# local (no packaging tools needed)
./install.sh install
./install.sh uninstall
```

`./install.sh install` puts a `zaribox` launcher in `~/.local/bin` and the code in `~/.local/lib/zaribox` (add `~/.local/bin` to your `PATH` if it isn't there). Use `--python <exe>` to pick a specific Python interpreter.
