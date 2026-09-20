# ZariBox

![ZariBox](zaribox.svg)

ZariBox is a declarative [Podman](https://podman.io/) container manager for both human development environments and AI-agent workflows. A YAML manifest describes the image, packages, setup, resources, mounts, and security policy; ZariBox creates the container and keeps it aligned with that description.

It supports two related workflows:

- **Development boxes** are persistent, interactive environments for people. They support shells, graphical applications, a dedicated home directory, and optional host-home integration.
- **AgentBoxes** are project-scoped environments for AI agents. They use versioned manifests, restricted mounts, an unprivileged runtime user, resource limits, bounded command execution, optional expiry, and an MCP server for agent tooling.

Both use the same lifecycle: validate the manifest, preview changes, create or update the container, inspect its state, and remove it without deleting its dedicated home directory. Destructive reconciliation is never implicit: package removal and container recreation require explicit approval.

## Development box quick start

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
zaribox create devbox.yaml   # create or update the container
zaribox enter devbox         # open a shell inside it
zaribox status devbox        # inspect configuration and package state
```

Run `zaribox` with no arguments for a concise overview, or `zaribox COMMAND --help` for detailed behavior, arguments, safety notes, and examples.

### Declarative lifecycle

The manifest is the source of truth. A typical workflow is:

```bash
zaribox validate devbox.yaml  # check the manifest without changing anything
zaribox plan devbox.yaml      # preview reconciliation and destructive actions
zaribox create devbox.yaml    # create or update the box
zaribox status devbox         # compare desired and applied state
```

`create` is idempotent: running it again updates only what changed. Use `--force` when a plan includes destructive reconciliation, or `--recreate` when you intentionally want to rebuild the container. The dedicated container home persists across updates, recreation, and removal.

ZariBox uses Podman as its container runtime. Podman must be installed and available in `PATH`.

## Commands

`MANIFEST` is a YAML file. `TARGET` can be either a managed container name or its manifest path. When an optional manifest is omitted, ZariBox looks for YAML files in the current directory. Commands that support `--json` emit one machine-readable response, making the same CLI suitable for scripts and agent orchestration.

| Command | Description |
| --- | --- |
| `zaribox validate [MANIFEST]` | Validate a manifest and report its resolved metadata without creating state. |
| `zaribox plan [TARGET]` | Show deterministic reconciliation actions without changing anything. |
| `zaribox create [TARGET]` | Create or update a container. Destructive changes require `--force`; use `--recreate` to rebuild it explicitly. |
| `zaribox status TARGET` | Report configuration, image, security policy, and package reconciliation state. |
| `zaribox exec TARGET -- COMMAND ...` | Run a bounded non-interactive command. Supports timeout, output limit, workdir, environment, and JSON results. |
| `zaribox export TARGET` | Fetch explicitly installed packages and atomically merge new ones into the manifest. |
| `zaribox enter NAME` | Open an interactive shell inside a desktop container. |
| `zaribox list` | List all managed containers recorded in ZariBox project state and their runtime state. |
| `zaribox remove TARGET` | Destroy a container after confirmation. Use `--force` for non-interactive use; its home directory is preserved. |
| `zaribox cleanup` | Remove expired AgentBoxes and stale operation leases. |

The commands are shared where that is safe. Development boxes can use `enter` for an interactive shell; AgentBoxes reject interactive entry and instead use bounded, non-interactive `exec`. The MCP interface exposes the AgentBox-safe subset rather than the interactive desktop workflow.

## Development box manifest

```yaml
Name: devbox          # optional, defaults to the file name
Image: archlinux      # required
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
| `HomeDir` | Home directory for the container; environment variables like `$USER` are expanded. Defaults to `$XDG_DATA_HOME/zaribox/home/<name>` (usually `~/.local/share/zaribox/home/<name>`). Persists across recreations. |
| `HomeMount` | Desktop-only compatibility option. When `true`, mounts the host home directory read-write at the same absolute path. It is rejected for AgentBox manifests. Host home is never mounted implicitly. |
| `ExtraFlags` | Extra flags passed through to `podman create`. |
| `Packages` | Packages to install when the container is created or synced. |
| `Run` | Shell commands executed as your user inside the container after package install. |

The package manager is inferred from known image names. For unknown images ZariBox probes for a supported package manager inside the container instead of assuming apt. `export` reads the distro's list of *explicitly installed* packages, so packages pulled in as dependencies are not added to your config.

### GUI applications

Development box manifests mount the host's X11/Wayland runtime sockets and forward graphical environment values. AgentBox manifests disable all graphical integration.

## AgentBox for AI agents

Use a versioned `AgentBox` manifest when an AI agent needs a reproducible workspace with narrower host access than an interactive development box. AgentBox disables host-home and GUI integration, restricts mounts, drops Linux capabilities, applies resource limits, runs agent commands as an unprivileged user, and supports automatic expiry.

The operator defines the environment and its boundaries in the manifest. An agent can then inspect the plan, create the box, execute bounded commands, read structured results, and remove the box without receiving an interactive shell or unrestricted host access.

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
  Packages:
    - git
    - ripgrep
  # Runs once after creation, as the unprivileged container user.
  Run:
    - python --version
Resources:
  CPUs: 2
  Memory: 2GiB
Security:
  Profile: agent
  Network: none
```

A complete example is available at [`examples/agentbox.yaml`](examples/agentbox.yaml), with its machine-readable definition in [`schema/agent-box-v1.schema.json`](schema/agent-box-v1.schema.json).

### AgentBox lifecycle

The regular CLI and MCP tools use the same underlying operations. JSON output is useful when a process, rather than a person, consumes the result:

```bash
zaribox validate agentbox.yaml --json
zaribox create agentbox.yaml --json
zaribox exec coding-agent --json -- pytest -q
zaribox status coding-agent --json
zaribox remove coding-agent --force --json
```

The recommended sequence is `validate` → `plan` → `create` → `exec`/`status` → `remove`. A plan reports whether creation needs destructive approval; pass `--force` only after reviewing those actions.

AgentBox can only mount paths inside the manifest's directory. Operators can allow additional host directories with the colon-separated `ZARIBOX_ALLOWED_MOUNT_ROOTS` environment variable. This prevents an agent from mounting arbitrary host paths such as the user's home directory.

`Runtime.Packages` uses the image's package manager and therefore runs as root inside the container. Package entries are validated to prevent package-manager options. `Runtime.Run` and commands executed by the agent run as an unprivileged user without sudo access.

Package downloads require `Security.Network: slirp4netns`. This network remains enabled while the AgentBox is running. For stronger isolation, build the required packages into an image and use `Security.Network: none`.

### MCP server for agentic workflows

The optional MCP 2.x stdio server lets an MCP-capable agent manage AgentBoxes through structured tools instead of constructing shell commands. It is deliberately scoped to AgentBoxes under one project root and does not expose interactive shells, desktop boxes, host-home access, or root command execution.

Install and run it from a source checkout:

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

The server exposes these tools:

| MCP tool | Purpose |
| --- | --- |
| `zaribox_validate` | Validate an AgentBox manifest and return its resolved metadata. |
| `zaribox_plan` | Preview reconciliation actions and report whether destructive approval is required. |
| `zaribox_create` | Create or update an AgentBox; destructive changes require `allow_destructive=true`. |
| `zaribox_status` | Report runtime, configuration, image, security, expiry, and package state. |
| `zaribox_exec` | Run a bounded argument vector as the unprivileged AgentBox user and return structured output. |
| `zaribox_list` | List AgentBoxes belonging to the configured project root. |
| `zaribox_remove` | Remove an AgentBox only when `confirm=true`; its dedicated home is preserved. |

Each tool includes agent-readable descriptions of its parameters, result, limits, and safety requirements. The server instructions recommend the same `validate` → `plan` → `create` workflow as the CLI. Paths are restricted to `ZARIBOX_MCP_ROOT`, command output is capped at 1 MiB, and execution time is bounded by `ZARIBOX_MCP_MAX_TIMEOUT` (900 seconds by default).

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

## Development

Enter the dev shell, install dependencies, and run the checks:

```bash
nix develop
uv sync --extra dev
uv run pytest -q
uv run ruff check .
```
