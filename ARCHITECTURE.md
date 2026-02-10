# SuperMCP Architecture

## Overview

SuperMCP is a single MCP server that orchestrates many child MCP servers.
An AI client connects to SuperMCP, and SuperMCP routes tool calls to whichever
child server owns that tool.

```
┌──────────────┐
│  AI Client   │  (Cursor, Claude, etc.)
└──────┬───────┘
       │ stdio
       ▼
┌──────────────┐      SUPERMCP_REGISTRY         registry file
│   SuperMCP   │ ───► (env var / .env) ───► mcpServers { … }
└──────┬───────┘                                   │
       │                                           ▼
       │         ┌───────────┐  ┌───────────┐  ┌───────────┐
       └────────►│ ServerA   │  │ ServerB   │  │ ServerC   │  …
                 │ (stdio)   │  │ (sse)     │  │ (stdio)   │
                 └───────────┘  └───────────┘  └───────────┘
```

## Configuration Flow

1. SuperMCP checks for `SUPERMCP_REGISTRY` — first as an environment variable,
   then in a `.env` file next to `SuperMCP.py`.
2. The value is a path (absolute or relative) to a registry JSON file.
3. The registry file contains a `mcpServers` object listing every child server,
   its type, command/args or URL, etc.
4. Relative paths inside the registry resolve from the registry file's
   directory — so the registry and its servers can live anywhere on disk.

## File Layout

```
SuperMCP/
├── SuperMCP.py          Main server — loads config, manages child servers
├── server_manager.py    Utilities: Git clone, SSE test, dependency install
├── .env.example         Template: SUPERMCP_REGISTRY=
├── .env                 (gitignored) User's actual config
├── pyproject.toml       Python dependencies
├── README.md            Usage documentation
└── ARCHITECTURE.md      This file
```

The registry file (wherever it lives) might look like:

```
/some/path/
├── mcp.json             Registry: { "mcpServers": { … } }
└── .mcps/
    ├── ShellMCP/
    │   └── server.py
    ├── CodeAnalysisMCP/
    │   └── server.py
    └── remote/           Git-cloned servers land here
        └── weather-mcp/
            └── server.py
```

## Transport Support

| Type  | How it connects | Config fields |
|-------|-----------------|---------------|
| stdio | Launches a local process | `command`, `args` |
| SSE   | Connects to a remote HTTP endpoint | `url`, optional `env` |

## Tools Exposed

| Tool | Purpose |
|------|---------|
| `reload_servers` | Re-read the registry file |
| `list_servers` | List what's currently loaded |
| `inspect_server` | Query a server's tools / prompts / resources |
| `call_server_tool` | Execute a tool on a child server |
| `add_server` | Add a new entry to the registry |
| `remove_server` | Delete an entry from the registry |
| `update_server` | Modify an existing entry |

## Key Design Decisions

- **Registry lives outside SuperMCP.** This makes SuperMCP a reusable module.
  Point it at any registry and it works — no need to copy servers into the
  SuperMCP directory.

- **Env var + `.env` fallback.** The host (Cursor, etc.) can pass
  `SUPERMCP_REGISTRY` as an environment variable, or the developer can set it
  in a local `.env` file. No dedicated config file format to learn.

- **Cached sub-server connections.** Stdio sub-servers are kept alive between
  calls for speed. When switching to a different server the old one is
  disconnected.

- **File-only logging.** MCP uses stdio for its protocol, so stderr output
  would corrupt messages. Logs go to `supermcp.log` by default; set
  `SUPERMCP_DEBUG=1` to also print to stderr.
