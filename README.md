# ZariBox

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

System-wide (default prefix `/usr/local`):

```bash
sudo ./install.sh install
```

User-local (no sudo, installs to `~/.local`):

```bash
./install.sh install --user
```

Custom prefix:

```bash
./install.sh install --prefix /opt/zaribox
```

After install:

```bash
zaribox help
```

## Uninstall

System-wide uninstall:

```bash
sudo ./install.sh uninstall
```

User-local uninstall:

```bash
./install.sh uninstall --user
```

Custom prefix uninstall:

```bash
./install.sh uninstall --prefix /opt/zaribox
```
