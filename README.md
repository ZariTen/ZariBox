# ZariBox

![ZariBox](zaribox.svg)

Declarative container manager for reproducible dev boxes. Reads a YAML file and keeps a container in sync with its image, packages, and bootstrap commands.

1. `ZARIBOX_BACKEND` environment variable
2. `Backend:` field in YAML
3. default: `distrobox`

## Commands

```bash
zaribox create  [file.yaml]
zaribox status  [container]
zaribox list
zaribox export    [container]
zaribox apply    [container]
zaribox enter   [container]
zaribox destroy [container]
```

- **`create`** — Create a new container from a YAML config
- **`status`** — Show sync status with package drift
- **`list`** — List all ZariBox-managed containers
- **`export`** — Sync packages from container into config file
- **`apply`** — Sync container to match config
- **`enter`** — Enter container (auto-apply if needed)
- **`destroy`** — Remove container (home dir preserved)

Auto-selects the only `*.yaml`/`*.yml` in the current directory if no file is passed to `create`.

## YAML

```yaml
Name: devbox # optional, defaults to filename
Image: archlinux # required
Backend: podman # optional
HomeDir: /home/user/.local/share/zaribox/homes/devbox
ExtraFlags: --device nvidia.com/gpu=all
Packages:
  - git
  - neovim
Run:
  - echo 'exec fish' >> ~/.bashrc
```

## Install

```bash
# Nix (recommended)
nix run github:ZariTen/ZariBox

# pip
pip install git+https://github.com/ZariTen/ZariBox.git

# local
./install.sh install
```

**Requirements:** Python 3.10+, PyYAML, and `distrobox` or `podman`.
