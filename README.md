# ZariBox

![ZariBox](zaribox.svg)

ZariBox is a declarative container manager for reproducible dev boxes. You describe a container in a YAML file (base image, packages, post-install setup), and ZariBox creates it and keeps it in sync with that description.

It uses [Podman](https://podman.io/) to run and manage containers.

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
3. default: `podman`

The only supported backend is `podman`, which must be installed and in `PATH`.

## Commands

All commands except `create` take a container name: ZariBox remembers which YAML file each container was created from (stored under `~/.config/zaribox`).

| Command | Description |
| --- | --- |
| `zaribox create [file.yaml]` | Create a container from a YAML config. If the container already exists it is recreated (home dir is preserved). With no file argument, the only `*.yaml`/`*.yml` in the current directory is used; if there are several, you must pass one explicitly. |
| `zaribox apply <name>` | Sync the container to match the config: install packages that are missing, remove ones that were dropped from `Packages:`. |
| `zaribox status <name>` | Show sync status: whether the container exists, whether the config changed since the last sync, and package drift in both directions. |
| `zaribox export <name>` | Fetch the packages you installed by hand inside the container and merge them into the `Packages:` block of the YAML file. |
| `zaribox enter <name>` | Open a shell inside the container. If the current host directory is covered by a container bind mount, enter there using its container path; otherwise use the container's configured home directory. |
| `zaribox list` | List all ZariBox-managed containers, marking which ones are currently running. |
| `zaribox remove <name>` | Destroy the container after a confirmation prompt. The home directory is preserved; ZariBox's state for it is cleared. |

## YAML reference

```yaml
Name: devbox          # optional, defaults to the file name
Image: archlinux      # required
Backend: podman       # optional, see "Backend selection"
HomeDir: /home/$USER/Documents/devbox   # optional
HomeMount: true                         # optional, mount /home/$USER at /run/host/home/$USER
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
| `Backend` | `podman`. |
| `HomeDir` | Home directory for the container; environment variables like `$USER` are expanded. Defaults to `$XDG_DATA_HOME/zaribox/home/<name>` (usually `~/.local/share/zaribox/home/<name>`). Persists across recreations. |
| `HomeMount` | When `true`, mounts the host home directory `/home/$USER` read-write at `/run/host/home/$USER` inside the container. `zaribox enter` uses the matching `/run/host/home/$USER/...` path when launched from a directory under the host home. |
| `ExtraFlags` | Extra flags passed through to `podman create`. |
| `Packages` | Packages to install when the container is created or synced. |
| `Run` | Shell commands executed as your user inside the container after package install. |

The package manager is auto-detected from the image name: `arch`/`manjaro`/`endeavour` → pacman, `ubuntu`/`debian`/`pop`/`mint` → apt, `fedora`/`centos`/`rhel` → dnf, `opensuse`/`suse` → zypper, `alpine` → apk, `void` → xbps. Unknown images fall back to apt. `export` reads the distro's list of *explicitly installed* packages, so packages pulled in as dependencies are not added to your config.

### GUI applications

On Linux, ZariBox mounts the host's X11/Wayland runtime sockets when the container is created and forwards the current `DISPLAY`, `WAYLAND_DISPLAY`, `XDG_SESSION_TYPE`, and `XDG_RUNTIME_DIR` values whenever it enters or executes a command. If those variables are not exported by the host shell, an unambiguous active socket is detected automatically. Containers created before the graphical runtime mount was available must be recreated for GUI applications to work.

## Install

**Requirements:** Python 3.10+, PyYAML, and `podman` in your `PATH`.

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
