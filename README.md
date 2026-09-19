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
| `zaribox validate [file.yaml]` | Strictly validate and normalize a manifest without creating state. |
| `zaribox plan [file-or-name]` | Show deterministic reconciliation actions without changing anything. |
| `zaribox ensure [file.yaml]` | Create or reconcile a container. Destructive changes require `--force`. |
| `zaribox create [file.yaml]` | Compatibility command that always recreates an existing container while preserving its dedicated home directory. |
| `zaribox apply <name>` | Reconcile a managed container; package removals or recreation require `--force`. |
| `zaribox inspect <name>` / `status` | Return container, configuration, image, policy, and package drift state. |
| `zaribox exec <name> -- command ...` | Run a bounded non-interactive command. Supports timeout, output limit, workdir, environment, and JSON results. |
| `zaribox export <name>` | Fetch explicitly installed packages and atomically merge them into the manifest. |
| `zaribox enter <name>` | Open an interactive shell inside a desktop container. |
| `zaribox list` | List project-scoped managed containers and their runtime state. |
| `zaribox remove <name>` | Destroy the container after confirmation. Use `--force` for non-interactive use; the home directory is preserved. |
| `zaribox cleanup` | Remove expired AgentBoxes and stale operation leases. |

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
| `HomeMount` | Desktop-only compatibility option. When `true`, mounts the host home directory read-write at the same absolute path. It is rejected for AgentBox manifests. Host home is never mounted implicitly. |
| `ExtraFlags` | Extra flags passed through to `podman create`. |
| `Packages` | Packages to install when the container is created or synced. |
| `Run` | Shell commands executed as your user inside the container after package install. |

The package manager is inferred from known image names. For unknown images ZariBox probes for a supported package manager inside the container instead of assuming apt. `export` reads the distro's list of *explicitly installed* packages, so packages pulled in as dependencies are not added to your config.

### GUI applications

Desktop manifests mount the host's X11/Wayland runtime sockets and forward graphical environment values. AgentBox manifests disable all graphical integration.

## AgentBox for AI agents

Use a versioned `AgentBox` manifest when an agent needs an isolated workspace. Unlike legacy desktop boxes, AgentBox disables host-home and GUI access, restricts mounts, drops Linux capabilities, applies resource limits, and supports automatic expiry.

```yaml
ApiVersion: zaribox.dev/v1
Kind: AgentBox
Metadata:
  Name: coding-agent
  TTL: 1h
Workspace:
  Mounts:
    - Source: .
      Target: /workspace
Runtime:
  Image: python:3.12
  Workdir: /workspace
Resources:
  CPUs: 2
  Memory: 2GiB
Security:
  Profile: agent
  Network: none
```

A complete example is available at [`examples/agentbox.yaml`](examples/agentbox.yaml), with its machine-readable definition in [`schema/agent-box-v1.schema.json`](schema/agent-box-v1.schema.json).

```bash
zaribox validate agentbox.yaml --json
zaribox ensure agentbox.yaml --json
zaribox exec coding-agent --json -- pytest -q
zaribox inspect coding-agent --json
zaribox remove coding-agent --force --json
```

AgentBox mounts are limited to the manifest directory unless the operator sets `ZARIBOX_ALLOWED_MOUNT_ROOTS`. Use `Security.Network: slirp4netns` when provisioning needs internet access; keep `none` for prebuilt images. Non-interactive commands support structured `--json` output, timeouts, and output limits.

### MCP server

Install and run the MCP 2.x stdio server:

```bash
pip install '.[mcp]'
ZARIBOX_MCP_ROOT="$PWD" zaribox-mcp
```

Example client configuration:

```json
{
  "mcpServers": {
    "zaribox": {
      "command": "zaribox-mcp",
      "env": {
        "ZARIBOX_MCP_ROOT": "/absolute/path/to/project"
      }
    }
  }
}
```

The server provides tools to validate, plan, create, inspect, execute in, list, and destroy AgentBoxes. Access is limited to the configured project root. You can also start it with `zaribox mcp`.

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
