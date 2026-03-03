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

If no file is passed, ZariBox auto-selects the first `*.yaml` / `*.yml` in the current directory.

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
- A container runtime backend:
	- `distrobox` (default), or
	- `podman`

## Install

Local install (copies project to `~/.local/lib/zaribox` and installs launcher at `~/.local/bin/zaribox`):

```bash
./install.sh install
```

With custom Python executable for launcher:

```bash
./install.sh install --python python3
```

After install:

```bash
zaribox help
```

Ensure `~/.local/bin` is in your `PATH`.

## Uninstall

```bash
./install.sh uninstall
```
