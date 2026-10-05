# MCP client recipes

Short, copy-paste setups for connecting an MCP-capable client to `zaribox-mcp`. The server only manages **AgentBoxes** under `ZARIBOX_MCP_ROOT`; desktop boxes, interactive shells, and host-home mounts are intentionally unavailable.

| Client | Recipe |
| --- | --- |
| [Cursor](cursor.md) | Project or user MCP config |
| [Claude Desktop](claude-desktop.md) | `claude_desktop_config.json` |

Install `zaribox` and `zaribox-mcp` first (see the [main README](../../README.md#install)), or run the server with Nix without installing:

```bash
ZARIBOX_MCP_ROOT="/absolute/path/to/project" nix run github:ZariTen/ZariBox#zaribox-mcp
```

## AgentBox hello-world

From the project you want the agent to work in:

```bash
cd /absolute/path/to/project
zaribox init --agent              # writes ./agentbox.yaml (Python template)
zaribox validate agentbox.yaml
zaribox plan agentbox.yaml
zaribox create agentbox.yaml      # add --force only if plan requires it
```

Point the client's `ZARIBOX_MCP_ROOT` at that same absolute project path, then use the MCP tools (`zaribox_validate` → `zaribox_plan` → `zaribox_create` → `zaribox_exec`) instead of constructing shell commands.
