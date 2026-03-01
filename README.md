# ZariBox

![ZariBox](zaribox.svg)

Declarative Distrobox manager with a split layout:
- `zaribox` (entrypoint)
- `zaribox.d/` (modules)

## Backend

ZariBox uses a backend abstraction for container runtime operations.

- Currently supported backends: `distrobox`, `podman`
- Selection precedence:
	1. `ZARIBOX_BACKEND` environment variable
	2. `Backend:` field in YAML
	3. default `distrobox`

Example:

```yaml
Name: archbox
Backend: podman
Image: archlinux
```

## Install

Local install (always installs into `~/.local`):

```bash
./install.sh install
```

With custom python executable for launcher:

```bash
./install.sh install --python python3
```

After install:

```bash
zaribox help
```

## Uninstall

```bash
./install.sh uninstall
```
