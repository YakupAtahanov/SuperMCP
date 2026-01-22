# SuperMCP

SuperMCP is a powerful orchestration layer for Model Context Protocol (MCP) servers that enables AI assistants to dynamically discover, inspect, and interact with multiple MCP servers through a unified interface.

## Overview

SuperMCP acts as a central hub that manages multiple MCP servers, allowing AI assistants to expand their capabilities on-demand by accessing specialized tools from various servers. Instead of being limited to static functionality, AI assistants can now leverage a growing ecosystem of MCP servers to handle diverse tasks.

## Core Features

### Dynamic Server Management
- **JSON Configuration**: Centralized `mcp.json` file for server management (similar to Cursor's approach)
- **Dual Transport Support**: Supports both SSE (Server-Sent Events) remote servers and stdio local servers
- **SSE Variable Support**: Environment variables for SSE servers passed as HTTP headers (like Cursor)
- **Runtime inspection**: Examine available tools, prompts, and resources from any server
- **Hot reloading**: Add new servers without restarting the system
- **AI-Driven Management**: AI can add, remove, and update servers dynamically
- **Git Integration**: Clone and manage Git-based MCP servers automatically
- **Marketplace Provider System**: Discover and install servers from trusted providers (Fedora Discover-style)
- **Unified interface**: Access all servers through consistent SuperMCP commands

### Available Commands

**Server Management:**
- `list_servers` - View all configured MCP servers from mcp.json
- `inspect_server` - Get detailed information about a server's capabilities
- `call_server_tool` - Execute tools from any available server
- `reload_servers` - Reload servers from mcp.json
- `add_server` - Add a new server (SSE or stdio) to the configuration
- `remove_server` - Remove a server from the configuration
- `update_server` - Update a server's configuration

**Marketplace Provider Management:**
- `list_providers` - List all configured marketplace providers
- `add_provider` - Add a new marketplace provider (static or API)
- `remove_provider` - Remove a provider from configuration
- `list_provider_servers` - List all servers from a provider
- `search_provider_servers` - Search servers across providers
- `get_provider_server` - Get detailed information about a specific server
- `install_from_provider` - Install a server from a marketplace provider

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        SuperMCP                                │
│                   (Orchestration Layer)                        │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Available MCPs                              │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────┐ │
│  │  ShellMCP   │  │CodeAnalysis │  │FileSystem   │  │ EchoMCP │ │
│  │             │  │    MCP      │  │    MCP      │  │         │ │
│  │ • Terminal  │  │ • Code      │  │ • File      │  │ • Test  │ │
│  │   Commands  │  │   Analysis  │  │   Ops       │  │ • Echo  │ │
│  │ • Package   │  │ • File      │  │ • Directory │  │ • Valid │ │
│  │   Install   │  │   Reading   │  │   Mgmt      │  │ • Temp  │ │
│  │ • Security  │  │ • Structure │  │ • Cross-    │  │         │ │
│  │   Controls  │  │   Analysis  │  │   Platform  │  │         │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

For detailed architecture documentation, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Potential Use Cases

### Content Creation
- Email drafting with personalization
- Document generation with templates
- Creative writing with style guides
- Marketing copy with brand guidelines

### Data Management
- Database operations across multiple systems
- File processing and organization
- API integrations and data synchronization
- Real-time analytics and reporting

### Development Tools
- Code generation and review
- Testing and deployment automation
- Documentation generation
- Performance monitoring

### Personal Productivity
- Calendar and scheduling management
- Task automation workflows
- Contact management and CRM
- Knowledge base organization

## Future Improvements

### 1. MCP Registry Integration
**Vision**: Connect to official MCP registries for automatic server discovery and installation.

**Implementation**:
- Add `search_registry(query)` - Search available MCP servers
- Add `download_mcp(name)` - Download and install MCP servers
- Add `update_mcp(name)` - Update existing servers
- Add `remove_mcp(name)` - Uninstall servers

**Benefits**:
- Access to entire MCP ecosystem
- Self-extending AI capabilities
- Community-driven functionality expansion

### 2. Intelligent Server Routing
**Vision**: AI assistant automatically determines which servers to use based on request context.

**Implementation**:
- Intent classification for server selection
- Multi-server orchestration for complex tasks
- Fallback mechanisms for unavailable servers
- Performance-based server prioritization

### 3. Enhanced Security & Sandboxing
**Vision**: Secure execution environment for third-party MCP servers.

**Implementation**:
- Permission-based access control
- Resource usage monitoring and limits
- Server isolation and containerization
- Audit logging for all server interactions

### 4. Configuration Management
**Vision**: Centralized configuration for all MCP servers.

**Implementation**:
- Global configuration file (`supermcp.config.json`)
- Environment-specific settings
- Server dependency management
- Version compatibility checking

### 5. Performance Optimization
**Vision**: High-performance server management with caching and pooling.

**Implementation**:
- Server connection pooling
- Response caching mechanisms
- Lazy loading of infrequently used servers
- Parallel execution for independent operations

### 6. Web Interface & Monitoring
**Vision**: Visual dashboard for managing and monitoring MCP servers.

**Implementation**:
- Real-time server status monitoring
- Performance metrics and analytics
- Visual server management interface
- Request/response logging and debugging

### 7. Advanced Orchestration
**Vision**: Complex workflow management across multiple servers.

**Implementation**:
- Workflow definition language
- Inter-server communication protocols
- State management across server calls
- Transaction rollback capabilities

### 8. AI-Powered Server Discovery
**Vision**: Intelligent recommendations for which MCP servers to install.

**Implementation**:
- Usage pattern analysis
- Contextual server suggestions
- Automated server installation based on user behavior
- Community rating and review system

## Development Roadmap

### Phase 1: Foundation (Current)
- ✅ Basic server discovery and management
- ✅ Tool execution interface
- ✅ Server inspection capabilities

### Phase 2: Expansion
- [ ] MCP registry integration
- [ ] Enhanced error handling and logging
- [ ] Configuration management system
- [ ] Basic performance optimizations

### Phase 3: Intelligence
- [ ] Intelligent server routing
- [ ] Workflow orchestration
- [ ] Advanced security features
- [ ] Web-based management interface

### Phase 4: Ecosystem
- [ ] Community features and sharing
- [ ] Advanced analytics and monitoring
- [ ] AI-powered recommendations
- [ ] Enterprise-grade features

## Technical Considerations

### Scalability
- Design for handling hundreds of MCP servers
- Efficient resource management and cleanup
- Horizontal scaling capabilities

### Reliability
- Graceful error handling and recovery
- Server health monitoring and alerting
- Backup and disaster recovery procedures

### Extensibility
- Plugin architecture for custom functionality
- API for third-party integrations
- Standardized server development templates

## Getting Started

1. Clone the SuperMCP repository
2. Set up your Python environment
3. Configure servers in `mcp.json` (see Configuration section below)
4. Use `list_servers` to verify server detection
5. Start building with `inspect_server` and `call_server_tool`

## Configuration

SuperMCP uses `mcp.json` for server configuration, similar to Cursor's MCP server management.

### Server Types

**SSE Servers** (Remote):
```json
{
  "mcpServers": {
    "remote-server": {
      "url": "http://example.com:8000/sse",
      "type": "sse",
      "description": "Remote SSE server",
      "enabled": true
    },
    "deepwiki": {
      "url": "https://api.deepwiki.com/mcp/sse",
      "type": "sse",
      "description": "DeepWiki MCP Server",
      "enabled": true,
      "env": {
        "API_KEY": "your-api-key",
        "REPO_NAME": "owner/repo"
      }
    }
  }
}
```

**SSE Server Environment Variables:**
SSE servers can include an `env` field with environment variables that are passed as HTTP headers. Variables are converted to headers in the format `X-MCP-{VAR_NAME}` (e.g., `API_KEY` becomes `X-MCP-API-KEY`).

**Stdio Servers** (Local):
```json
{
  "mcpServers": {
    "local-server": {
      "command": "python",
      "args": [".mcps/ShellMCP/server.py"],
      "type": "stdio",
      "description": "Local stdio server",
      "enabled": true
    }
  }
}
```

**Git-based Stdio Servers**:
```json
{
  "mcpServers": {
    "git-server": {
      "command": "python",
      "args": [".mcps/remote/weather-mcp/server.py"],
      "type": "stdio",
      "url": "https://github.com/user/weather-mcp.git",
      "description": "Git-based server (cloned locally, runs as stdio)",
      "enabled": true
    }
  }
}
```

### Adding Servers

You can add servers manually by editing `mcp.json`, or use the AI tools:
- `add_server(name, server_type, url, command, args, description, env)` - Add a new server
  - For SSE servers: provide `url` and optional `env` dict for environment variables
  - For stdio servers: provide `command`, `args`, and optional `url` for Git repos
- `remove_server(name)` - Remove a server
- `update_server(name, **kwargs)` - Update server configuration (can update `env` for SSE servers)

### Marketplace Provider System

SuperMCP includes a marketplace provider system (similar to Fedora Discover) that allows you to discover and install MCP servers from trusted sources.

**Default Provider:**
- A static JSON catalog (`default_catalog.json`) is included as the default trusted provider
- Contains curated list of trusted MCP servers
- Works offline, no external dependencies

**Adding Providers:**
You can add additional providers using the `add_provider` tool:

```python
# Add a static provider (JSON file)
add_provider(
    provider_id="my-provider",
    name="My Custom Provider",
    provider_type="static",
    catalog_file="my_catalog.json",
    trusted=False
)

# Add an API provider
add_provider(
    provider_id="github-mcp",
    name="GitHub MCP Registry",
    provider_type="api",
    url="https://api.example.com/mcp/servers",
    trusted=False
)
```

**Installing Servers from Providers:**
```python
# List available servers
list_provider_servers()  # Uses default provider

# Search for servers
search_provider_servers("documentation")

# Get server details
get_provider_server("deepwiki")

# Install a server
install_from_provider(
    server_id="deepwiki",
    name="my-deepwiki",
    variables={
        "API_KEY": "your-key",
        "REPO_NAME": "owner/repo"
    }
)
```

**Provider Configuration:**
Providers are configured in `provider_config.json`:
```json
{
  "providers": [
    {
      "id": "default",
      "name": "JARVIS Trusted Provider",
      "type": "static",
      "catalog_file": "default_catalog.json",
      "trusted": true,
      "enabled": true
    }
  ],
  "default_provider": "default"
}
```

### Migration from Directory Scanning

If you have existing servers in the `.mcps` directory, you'll need to manually add them to `mcp.json`. The system no longer auto-discovers servers from the directory structure.

## Contributing

SuperMCP thrives on community contributions. Whether you're building new MCP servers, improving the core orchestration layer, or enhancing documentation, your contributions help expand the capabilities available to AI assistants worldwide.

---

*SuperMCP: Unleashing the full potential of AI through dynamic capability expansion.*

