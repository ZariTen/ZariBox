# ZariBox

![ZariBox](zaribox.svg)

Declarative container manager for reproducible dev boxes. Reads a YAML file and keeps a container in sync with its image, packages, and bootstrap commands.

1. `ZARIBOX_BACKEND` environment variable
2. `Backend:` field in YAML
3. default: `distrobox`

## Commands

```bash
zaribox status  [container]
zaribox list
zaribox apply   [file.yaml]
zaribox enter   [container]
zaribox destroy [container]
```

Auto-selects the only `*.yaml`/`*.yml` in the current directory if no file is passed.

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
