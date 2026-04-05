# ZariBox
![ZariBox](zaribox.svg)

Declarative container manager for reproducible dev boxes.

ZariBox reads a YAML file and keeps a container in sync with:

- container identity (`Image`, `HomeDir`, `ExtraFlags`)
- package set (`Packages`)
- optional post-install bootstrap commands (`Run`)

Supported backends:

- `distrobox`
- `podman`

Backend selection precedence:

1. `ZARIBOX_BACKEND` environment variable
2. `Backend:` field in YAML
3. default: `distrobox`

## Commands

```bash
zaribox status  [file.yaml]
zaribox list
zaribox apply   [file.yaml]
zaribox enter   [file.yaml]
zaribox destroy [file.yaml]
zaribox help
```

If no file is passed, ZariBox auto-selects the only `*.yaml` / `*.yml` in the current directory.
If multiple YAML files are present, pass one explicitly.

## YAML format

Minimal config:

```yaml
Name: archbox
Image: archlinux
```

Common fields:

- `Name` (optional): container name, defaults to YAML filename stem
- `Image` (required): image to create from
- `Backend` (optional): `distrobox` or `podman`
- `HomeDir` (optional): host directory mounted as container home
- `ExtraFlags` (optional): extra backend create flags
- `Packages` (optional): package list to install and reconcile
- `Run` (optional): commands executed after creation/package install

Example:

```yaml
Name: devbox
Backend: podman
Image: archlinux
HomeDir: /home/user/.local/share/zaribox/homes/devbox
ExtraFlags: --device nvidia.com/gpu=all

Packages:
    - git
    - neovim
    - fish

Run:
    - echo 'exec fish' >> ~/.bashrc
```

## Requirements

- Python 3.10+
- PyYAML
- A container runtime backend:
  - `distrobox` (default), or
  - `podman`

## Install

### Nix (recommended)

Try it without installing:

```bash
nix run github:ZariTen/ZariBox
```

Add to your system flake:

```nix
inputs.zaribox.url = "github:ZariTen/ZariBox";

# then in environment.systemPackages or home.packages:
zaribox.packages.x86_64-linux.default
```

### pip

```bash
pip install git+https://github.com/ZariTen/ZariBox.git
```

### Local (install.sh)

```bash
./install.sh install
```

Ensure `~/.local/bin` is in your `PATH`.

## Uninstall

### Nix

Remove from your system config and rebuild.

### pip

```bash
pip uninstall zaribox
```

### Local

```bash
./install.sh uninstall
```