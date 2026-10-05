# Claude Desktop

Add `zaribox-mcp` to Claude Desktop's MCP servers so Claude can manage AgentBoxes under one project root.

## 1. Prepare the project

```bash
cd /absolute/path/to/project
zaribox init --agent
zaribox validate agentbox.yaml
zaribox create agentbox.yaml
```

Use the **absolute** path for `ZARIBOX_MCP_ROOT` below.

## 2. Edit Claude's config

Config file locations:

| Platform | Path |
| --- | --- |
| Linux | `~/.config/Claude/claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

Merge (or create) an `mcpServers` entry:

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

`zaribox-mcp` must be on the `PATH` visible to Claude Desktop (for example `~/.local/bin` after `./install.sh install`).

### Without installing (Nix)

```json
{
  "mcpServers": {
    "zaribox": {
      "command": "nix",
      "args": ["run", "github:ZariTen/ZariBox#zaribox-mcp"],
      "env": {
        "ZARIBOX_MCP_ROOT": "/absolute/path/to/project"
      }
    }
  }
}
```

Restart Claude Desktop after saving the file.

## 3. Use it

Claude should list tools such as `zaribox_status` and `zaribox_exec` for the configured root. Follow `validate` → `plan` → `create` before running work inside the box. The server will not expose interactive `enter`, desktop manifests, or host-home mounts.

Optional: set `ZARIBOX_MCP_MAX_TIMEOUT` in `env` (seconds; default 900) if long builds need a higher ceiling.
