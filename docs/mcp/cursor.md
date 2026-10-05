# Cursor

Wire `zaribox-mcp` into Cursor so agents can manage AgentBoxes for one project root.

## 1. Prepare the project

```bash
cd /absolute/path/to/project
zaribox init --agent
zaribox validate agentbox.yaml
zaribox create agentbox.yaml
```

`ZARIBOX_MCP_ROOT` must be this directory's **absolute** path. Relative paths are easy to mis-resolve when Cursor launches the server.

## 2. MCP configuration

Add a server entry (project `.cursor/mcp.json`, or your user MCP settings -- Cursor's UI labels vary by version):

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

Ensure `zaribox-mcp` is on the `PATH` that Cursor inherits (for example after `./install.sh install` into `~/.local/bin`).

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

## 3. Use it

After Cursor reloads MCP servers, the agent should see tools such as `zaribox_plan`, `zaribox_create`, and `zaribox_exec`. Prefer `validate` -> `plan` -> `create` before `exec`. Destructive reconciliation needs `allow_destructive=true`; removal needs `confirm=true`.

Optional: raise the exec timeout ceiling with `ZARIBOX_MCP_MAX_TIMEOUT` (seconds; default 900) in the same `env` block.
