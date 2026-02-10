# SuperMCP

SuperMCP is an orchestration layer for Model Context Protocol (MCP) servers. It gives an AI assistant a single entry point to dynamically discover, inspect, and call tools across many MCP servers — without hard-coding anything.

## How It Works

```
SuperMCP starts
  └─ reads SUPERMCP_REGISTRY   (env var, or from .env file)
       └─ points to a registry file anywhere on disk
            └─ loads mcpServers from that file
                 └─ resolves relative paths from the registry's directory
```

Because the registry file can live **anywhere**, you can keep your servers and their configuration wherever makes sense — a project folder, a shared drive, a dotfiles repo — and just point SuperMCP at it.

## Quick Start

1. **Clone & install dependencies**

```bash
git clone https://github.com/YakupAtahanov/SuperMCP.git
cd SuperMCP
uv pip install "mcp[cli]"
```

2. **Configure the registry path**

```bash
cp .env.example .env
```

Edit `.env`:

```
SUPERMCP_REGISTRY=C:/Users/you/my-servers/mcp.json
```

The path can be absolute or relative (relative paths resolve from the SuperMCP directory).

Alternatively, pass it as an environment variable when launching:

```bash
SUPERMCP_REGISTRY=/path/to/mcp.json python SuperMCP.py
```

Or set it in your MCP host (e.g. Cursor):

```json
{
  "command": "python",
  "args": ["C:/path/to/SuperMCP.py"],
  "env": { "SUPERMCP_REGISTRY": "C:/Users/you/my-servers/mcp.json" }
}
```

3. **Create your registry file** at that location:

```json
{
  "mcpServers": {
    "ShellMCP": {
      "command": "python",
      "args": [".mcps/ShellMCP/server.py"],
      "type": "stdio",
      "description": "Shell command execution",
      "enabled": true
    }
  }
}
```

Relative paths inside the registry (like `.mcps/ShellMCP/server.py`) resolve from the registry file's directory.

4. **Run**

```bash
python SuperMCP.py
```

## Available Tools

| Tool | Description |
|------|-------------|
| `reload_servers` | Reload the registry and rebuild the in-memory server list |
| `list_servers` | List all registered MCP servers |
| `inspect_server` | Inspect a server's tools, prompts, and resources |
| `call_server_tool` | Call a tool on any registered server |
| `add_server` | Add a new server (SSE or stdio) to the registry |
| `remove_server` | Remove a server from the registry |
| `update_server` | Update a server's configuration |

## Server Types

### Stdio (local process)

```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["path/to/server.py"],
      "type": "stdio",
      "description": "A local MCP server",
      "enabled": true
    }
  }
}
```

### SSE (remote endpoint)

```json
{
  "mcpServers": {
    "remote-server": {
      "url": "https://example.com/mcp/sse",
      "type": "sse",
      "description": "A remote SSE server",
      "enabled": true,
      "env": {
        "API_KEY": "your-key"
      }
    }
  }
}
```

Environment variables in the `env` field are sent as HTTP headers in the format `X-MCP-{VAR_NAME}`.

### Git-based Stdio

```json
{
  "mcpServers": {
    "weather-mcp": {
      "command": "python",
      "args": [".mcps/remote/weather-mcp/server.py"],
      "type": "stdio",
      "url": "https://github.com/user/weather-mcp.git",
      "description": "Cloned from Git, runs locally",
      "enabled": true
    }
  }
}
```

When a stdio server has a `url` field, SuperMCP clones the repository into `.mcps/remote/<name>/` (relative to the registry) and installs its dependencies.

## Project Structure

```
SuperMCP/
├── SuperMCP.py          # Main orchestration server
├── server_manager.py    # Git cloning, SSE testing, dependency install
├── .env.example         # Template — copy to .env and set SUPERMCP_REGISTRY
├── pyproject.toml       # Python dependencies
├── ARCHITECTURE.md      # Architecture overview
└── README.md
```

## Contributing

Contributions welcome. Whether you're building new MCP servers, improving the orchestration layer, or enhancing documentation — all help is appreciated.

## License

See [LICENSE](LICENSE).
