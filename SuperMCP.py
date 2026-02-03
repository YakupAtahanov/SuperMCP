import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from mcp.server.fastmcp import FastMCP
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Try to import SSE client support
SSE_AVAILABLE = False
try:
    from mcp.client.sse import sse_client
    SSE_AVAILABLE = True
except ImportError:
    pass  # Will log after logger is initialized

# Configure logging
# Note: MCP servers use stdio protocol - stderr logging MUST be minimal/disabled
# to avoid corrupting protocol messages. Log only to file by default.
import os
log_file_path = os.path.join(os.path.dirname(__file__), 'supermcp.log')

# Only log to file, never to stderr (unless debugging)
log_handlers = [logging.FileHandler(log_file_path)]
if os.environ.get('SUPERMCP_DEBUG'):
    log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.WARNING,  # Only warnings and errors, not info
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger("SuperMCP")

HERE = Path(__file__).resolve().parent
MCPS_DIR = HERE / ".mcps"
MCP_CONFIG_PATH = HERE / "mcp.json"

mcp = FastMCP("SuperMCP")
logger.info("SuperMCP initialized with MCPS_DIR: %s, MCP_CONFIG_PATH: %s", MCPS_DIR, MCP_CONFIG_PATH)
if not SSE_AVAILABLE:
    logger.debug("SSE client not available in MCP SDK, will use httpx if needed")

# name -> { "type": "stdio" | "sse", "command": str | None, "args": List[str] | None,
#           "url": str | None, "path": str | None, "description": str | None, "enabled": bool }
REGISTRY: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# Persistent Sub-Server Cache
# =============================================================================
# Keeps the last used stdio server running for faster repeated calls.
# When switching to a different server, the old one is disconnected.

class CachedSubServer:
    """Holds a persistent connection to a sub-server (like ShellMCP)"""

    def __init__(self, name: str, process: 'subprocess.Popen', tools: List[str]):
        self.name = name
        self.process = process
        self.tools = tools
        self._request_id = 0

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def send_recv(self, request: dict) -> Optional[dict]:
        """Send a request and receive response"""
        if not self.is_alive():
            return None
        try:
            msg = json.dumps(request) + "\n"
            self.process.stdin.write(msg.encode())
            self.process.stdin.flush()
            response = self.process.stdout.readline().decode().strip()
            if response:
                return json.loads(response)
        except Exception as e:
            logger.error("CachedSubServer %s: send_recv failed: %s", self.name, e)
        return None

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call a tool on this cached server"""
        if not self.is_alive():
            return {"error": f"Server {self.name} is not running"}

        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not found. Available: {self.tools}"}

        result_resp = self.send_recv({
            "jsonrpc": "2.0",
            "id": self.next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {}
            }
        })

        if not result_resp:
            return {"error": "Empty response from server"}

        if "error" in result_resp:
            return {"error": result_resp["error"]}

        return result_resp.get("result", {})

    def disconnect(self):
        """Disconnect this sub-server"""
        if self.process:
            logger.info("Disconnecting cached sub-server: %s", self.name)
            try:
                self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=2)
            except:
                try:
                    self.process.kill()
                except:
                    pass
            self.process = None


# Global cache for the last used sub-server
_cached_subserver: Optional[CachedSubServer] = None


def _get_or_create_cached_subserver(server_name: str, command: str, args: List[str]) -> Optional[CachedSubServer]:
    """
    Get a cached sub-server connection, or create a new one.

    If switching to a different server, the old one is disconnected first.
    """
    global _cached_subserver

    # Check if we already have this server cached and alive
    if _cached_subserver is not None:
        if _cached_subserver.name == server_name and _cached_subserver.is_alive():
            logger.debug("Reusing cached sub-server: %s", server_name)
            return _cached_subserver
        else:
            # Different server or dead process - disconnect old one
            if _cached_subserver.name != server_name:
                logger.info("Switching sub-server: %s -> %s", _cached_subserver.name, server_name)
            else:
                logger.info("Sub-server %s died, reconnecting...", server_name)
            _cached_subserver.disconnect()
            _cached_subserver = None

    # Create new connection
    logger.info("Starting cached sub-server: %s", server_name)

    try:
        import subprocess

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            cwd=str(HERE),
            creationflags=creationflags
        )

        # Helper for init sequence
        req_id = [0]
        def next_id():
            req_id[0] += 1
            return req_id[0]

        def send_recv(request):
            msg = json.dumps(request) + "\n"
            process.stdin.write(msg.encode())
            process.stdin.flush()
            response = process.stdout.readline().decode().strip()
            if response:
                return json.loads(response)
            return None

        # Initialize
        init_resp = send_recv({
            "jsonrpc": "2.0",
            "id": next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "SuperMCP", "version": "1.0"}
            }
        })

        if not init_resp or "error" in init_resp:
            raise RuntimeError(f"Failed to initialize: {init_resp}")

        # Send initialized notification
        process.stdin.write((json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }) + "\n").encode())
        process.stdin.flush()

        # List tools
        tools_resp = send_recv({
            "jsonrpc": "2.0",
            "id": next_id(),
            "method": "tools/list"
        })

        available_tools = []
        if tools_resp and "result" in tools_resp:
            available_tools = [t["name"] for t in tools_resp["result"].get("tools", [])]

        # Create cached server object
        cached = CachedSubServer(server_name, process, available_tools)
        cached._request_id = req_id[0]  # Continue from where we left off

        _cached_subserver = cached
        logger.info("Cached sub-server %s ready with %d tools", server_name, len(available_tools))

        return cached

    except Exception as e:
        logger.error("Failed to start cached sub-server %s: %s", server_name, e)
        return None


def _disconnect_cached_subserver():
    """Disconnect the cached sub-server (called on shutdown)"""
    global _cached_subserver
    if _cached_subserver:
        _cached_subserver.disconnect()
        _cached_subserver = None


# Register cleanup on module unload (best effort)
import atexit
atexit.register(_disconnect_cached_subserver)


def _derive_name(p: Path) -> str:
    # use parent directory name as the server name
    return p.parent.name

def _detect_server_type(server_config: Dict[str, Any]) -> str:
    """
    Detect server type from configuration.
    
    Rules:
    - If only 'url' present and no 'command'/'args' -> SSE
    - If 'command'/'args' present -> stdio (even if 'url' exists for Git cloning)
    """
    has_url = "url" in server_config and server_config["url"]
    has_command = "command" in server_config and server_config["command"]
    has_args = "args" in server_config and server_config["args"]
    
    # Explicit type override
    if "type" in server_config:
        return server_config["type"]
    
    # Auto-detect: if command/args present, it's stdio
    if has_command and has_args:
        return "stdio"
    
    # If only URL present, it's SSE
    if has_url and not (has_command or has_args):
        return "sse"
    
    # Default to stdio if unclear (backward compatibility)
    return "stdio"

def _load_mcp_config() -> Dict[str, Any]:
    """
    Load MCP server configuration from mcp.json.
    
    Returns:
        Dict with mcpServers configuration
    """
    if not MCP_CONFIG_PATH.exists():
        logger.warning("mcp.json not found, creating empty configuration")
        empty_config = {"mcpServers": {}}
        try:
            with open(MCP_CONFIG_PATH, 'w') as f:
                json.dump(empty_config, f, indent=2)
            logger.info("Created empty mcp.json at %s", MCP_CONFIG_PATH)
        except Exception as e:
            logger.error("Failed to create mcp.json: %s", e)
        return empty_config
    
    try:
        with open(MCP_CONFIG_PATH, 'r') as f:
            config = json.load(f)
        
        if "mcpServers" not in config:
            logger.warning("mcp.json missing 'mcpServers' key, using empty dict")
            config["mcpServers"] = {}
        
        return config
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in mcp.json: %s", e)
        return {"mcpServers": {}}
    except Exception as e:
        logger.error("Failed to load mcp.json: %s", e)
        return {"mcpServers": {}}

def _resolve_path(path_str: str) -> Path:
    """
    Resolve a path string relative to SuperMCP directory or as absolute.
    
    Args:
        path_str: Path string (can be relative or absolute)
    
    Returns:
        Resolved Path object
    """
    path = Path(path_str)
    if path.is_absolute():
        return path
    # Resolve relative to SuperMCP directory
    return HERE / path_str

def _create_sse_headers(env: Optional[Dict[str, str]]) -> Dict[str, str]:
    """
    Convert environment variables to HTTP headers for SSE connections.
    
    Args:
        env: Dictionary of environment variables
    
    Returns:
        Dictionary of HTTP headers
    """
    if not env:
        return {}
    
    headers = {}
    for key, value in env.items():
        # Convert VAR_NAME to X-MCP-VAR-NAME format
        header_name = f"X-MCP-{key.upper().replace('_', '-')}"
        headers[header_name] = value
    
    return headers

def _mask_env_values(env: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """
    Mask environment variable values for logging (security).
    
    Args:
        env: Dictionary of environment variables
    
    Returns:
        Dictionary with masked values
    """
    if not env:
        return None
    return {key: "***" for key in env.keys()}

def _scan_available():
    """
    Load MCP servers from mcp.json configuration file.
    """
    logger.info("Loading MCP servers from mcp.json")
    REGISTRY.clear()
    
    # Load configuration
    config = _load_mcp_config()
    servers = config.get("mcpServers", {})
    
    if not servers:
        logger.warning("No servers found in mcp.json")
        return
    
    found_count = 0
    for name, server_config in servers.items():
        # Skip disabled servers
        if not server_config.get("enabled", True):
            logger.debug("Skipping disabled server: %s", name)
            continue
        
        # Detect server type
        server_type = _detect_server_type(server_config)
        
        # Validate required fields based on type
        if server_type == "sse":
            if "url" not in server_config or not server_config["url"]:
                logger.error("SSE server '%s' missing required 'url' field", name)
                continue
            
            REGISTRY[name] = {
                "type": "sse",
                "url": server_config["url"],
                "command": None,
                "args": None,
                "path": None,
                "description": server_config.get("description"),
                "enabled": True,
                "env": server_config.get("env")
            }
            logger.info("Registered SSE server: %s at %s", name, server_config["url"])
            found_count += 1
            
        elif server_type == "stdio":
            if "command" not in server_config or not server_config["command"]:
                logger.error("Stdio server '%s' missing required 'command' field", name)
                continue
            if "args" not in server_config or not server_config["args"]:
                logger.error("Stdio server '%s' missing required 'args' field", name)
                continue
            
            # Handle Git-based servers
            if "url" in server_config and server_config["url"]:
                # This is a Git-based stdio server
                # Import server_manager here to avoid circular imports
                from server_manager import clone_git_repo, install_dependencies
                
                # Determine target directory
                git_target = MCPS_DIR / "remote" / name
                
                # Clone if needed
                if not git_target.exists():
                    try:
                        logger.info("Cloning Git repository for server '%s'", name)
                        clone_git_repo(server_config["url"], git_target)
                        # Optionally install dependencies
                        install_dependencies(git_target)
                    except Exception as e:
                        logger.error("Failed to clone Git repository for '%s': %s", name, e)
                        continue
                
                # Resolve entry point path
                entry_point = server_config["args"][0] if server_config["args"] else None
                if entry_point:
                    entry_path = _resolve_path(entry_point)
                    if not entry_path.exists():
                        logger.error("Entry point not found for '%s': %s", name, entry_path)
                        continue
                else:
                    logger.error("No entry point specified for Git-based server '%s'", name)
                    continue
            else:
                # Local stdio server - resolve path
                entry_point = server_config["args"][0] if server_config["args"] else None
                if entry_point:
                    entry_path = _resolve_path(entry_point)
                    if not entry_path.exists():
                        logger.error("Entry point not found for '%s': %s", name, entry_path)
                        continue
                else:
                    logger.error("No entry point specified for server '%s'", name)
                    continue
            
            REGISTRY[name] = {
                "type": "stdio",
                "command": server_config["command"],
                "args": server_config["args"],
                "url": server_config.get("url"),
                "path": str(entry_path) if entry_point else None,
                "description": server_config.get("description"),
                "enabled": True
            }
            logger.info("Registered stdio server: %s", name)
            found_count += 1
        else:
            logger.error("Unknown server type '%s' for server '%s'", server_type, name)
            continue
    
    logger.info("Load complete. Found %d MCP server(s): %s", found_count, list(REGISTRY.keys()))

async def _inspect_once(server_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inspect a server's capabilities.
    
    Supports both stdio and SSE transport types.
    """
    server_type = server_config.get("type", "stdio")
    
    if server_type == "sse":
        # SSE server inspection
        url = server_config.get("url")
        if not url:
            raise ValueError("SSE server missing URL")
        
        env = server_config.get("env")
        headers = _create_sse_headers(env)
        masked_env = _mask_env_values(env)
        logger.debug("Inspecting SSE server at: %s (env: %s)", url, masked_env)
        
        try:
            # Try to use MCP SSE client if available
            if SSE_AVAILABLE:
                # Note: sse_client from mcp.client.sse may not support headers directly
                # We'll need to use httpx with SSE parsing or check if sse_client supports headers
                # For now, try with httpx if headers are needed
                if headers:
                    import httpx
                    from httpx_sse import EventSource
                    # Use httpx with SSE support when headers are needed
                    async with httpx.AsyncClient() as client:
                        async with EventSource(url, client=client, headers=headers) as event_source:
                            # Initialize connection and get capabilities
                            # This is a simplified approach - may need adjustment based on MCP SSE spec
                            async for event in event_source:
                                if event.event == "message":
                                    # Parse MCP messages
                                    pass
                            # Fallback to basic inspection
                            return {
                                "tools": [],
                                "prompts": [],
                                "resources": [],
                                "note": "SSE inspection with headers - full inspection may require direct MCP client support"
                            }
                else:
                    # Use MCP SSE client when no headers needed
                    async with sse_client(url) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        prompts = await session.list_prompts()
                        resources = await session.list_resources()
                        result = {
                            "tools": [t.name for t in getattr(tools, "tools", [])],
                            "prompts": [p.name for p in getattr(prompts, "prompts", [])],
                            "resources": [r.uri for r in getattr(resources, "resources", [])],
                        }
                        logger.info("SSE inspection successful: %d tools, %d prompts, %d resources",
                                   len(result["tools"]), len(result["prompts"]), len(result["resources"]))
                        return result
            else:
                # Fallback: use httpx for basic connection test
                import httpx
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, headers=headers, timeout=5.0)
                    return {
                        "tools": [],
                        "prompts": [],
                        "resources": [],
                        "note": "SSE client not available, connection test only",
                        "status_code": response.status_code
                    }
        except Exception as e:
            logger.error("Failed to inspect SSE server: %s", e, exc_info=True)
            raise
    
    else:
        # Stdio server inspection
        command = server_config.get("command")
        args = server_config.get("args")
        if not command or not args:
            raise ValueError("Stdio server missing command or args")
        
        logger.debug("Inspecting stdio server: command=%s, args=%s", command, args)
        try:
            params = StdioServerParameters(command=command, args=args)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    logger.debug("Initializing client session for inspection")
                    await session.initialize()
                    tools = await session.list_tools()
                    prompts = await session.list_prompts()
                    resources = await session.list_resources()
                    result = {
                        "tools": [t.name for t in getattr(tools, "tools", [])],
                        "prompts": [p.name for p in getattr(prompts, "prompts", [])],
                        "resources": [r.uri for r in getattr(resources, "resources", [])],
                    }
                    logger.info("Stdio inspection successful: %d tools, %d prompts, %d resources",
                               len(result["tools"]), len(result["prompts"]), len(result["resources"]))
                    return result
        except Exception as e:
            logger.error("Failed to inspect stdio server: %s", e, exc_info=True)
            raise

def _call_stdio_tool_cached(server_name: str, command: str, args: List[str], tool_name: str, arguments: dict) -> Any:
    """
    Call a tool on a stdio MCP server using a cached persistent connection.

    The sub-server (e.g., ShellMCP) is kept running for fast repeated calls.
    When switching to a different server, the old one is disconnected.
    """
    logger.debug("Calling tool '%s' on cached server '%s'", tool_name, server_name)

    # Get or create cached connection
    cached = _get_or_create_cached_subserver(server_name, command, args)
    if cached is None:
        return {"error": f"Failed to connect to server {server_name}"}

    # Call the tool
    result = cached.call_tool(tool_name, arguments or {})

    # Extract content from result
    if isinstance(result, dict):
        if "error" in result:
            return result

        # Handle structured content
        if "structuredContent" in result and result["structuredContent"] is not None:
            logger.info("Tool executed successfully (structured content)")
            return result["structuredContent"]

        # Handle text content
        content = result.get("content", [])
        if content:
            texts = [item.get("text", "") for item in content if isinstance(item, dict)]
            if texts:
                logger.info("Tool executed successfully (text content)")
                return "\n".join(texts)

    return result


async def _call_tool_once(server_name: str, server_config: Dict[str, Any], tool_name: str, arguments: dict) -> Any:
    """
    Call a tool on a server.

    Supports both stdio and SSE transport types.
    For stdio servers, uses a persistent cached connection.
    """
    server_type = server_config.get("type", "stdio")

    if server_type == "sse":
        # SSE server tool call
        url = server_config.get("url")
        if not url:
            raise ValueError("SSE server missing URL")
        
        env = server_config.get("env")
        headers = _create_sse_headers(env)
        masked_env = _mask_env_values(env)
        logger.info("Calling tool '%s' on SSE server at %s with arguments: %s (env: %s)", 
                   tool_name, url, arguments, masked_env)
        
        try:
            if SSE_AVAILABLE:
                # Note: sse_client may not support headers directly
                # For now, try without headers first, then fallback to httpx if headers needed
                if headers:
                    # Use httpx with SSE support when headers are needed
                    import httpx
                    from httpx_sse import EventSource
                    # This is a simplified approach - may need full MCP SSE implementation
                    async with httpx.AsyncClient() as client:
                        async with EventSource(url, client=client, headers=headers) as event_source:
                            # Send MCP tool call request
                            # This requires proper MCP SSE protocol implementation
                            # For now, return error indicating header support needed
                            return {
                                "error": "SSE servers with environment variables require custom implementation. "
                                        "Headers are prepared but MCP SSE client needs enhancement for header support."
                            }
                else:
                    # Use standard MCP SSE client when no headers needed
                    async with sse_client(url) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        names = [t.name for t in getattr(tools, "tools", [])]
                        if tool_name not in names:
                            logger.warning("Tool '%s' not found. Available tools: %s", tool_name, names)
                            return {"error": f"Tool '{tool_name}' not found. Available: {names}"}
                        
                        result = await session.call_tool(tool_name, arguments or {})
                        return _extract_result_content(result)
            else:
                return {"error": "SSE client not available. Please install MCP SDK with SSE support or use httpx."}
        except Exception as e:
            logger.error("Failed to call tool '%s' on SSE server: %s", tool_name, e, exc_info=True)
            raise
    
    else:
        # Stdio server tool call - using direct subprocess for Windows compatibility
        command = server_config.get("command")
        args = server_config.get("args")
        if not command or not args:
            raise ValueError("Stdio server missing command or args")

        logger.info("Calling tool '%s' with arguments: %s", tool_name, arguments)
        # Use cached persistent connection for stdio servers
        return _call_stdio_tool_cached(server_name, command, args, tool_name, arguments)

def _extract_result_content(result) -> Any:
    """Extract content from MCP result object."""
    # Prefer structured content
    if getattr(result, "structuredContent", None) is not None:
        logger.info("Tool executed successfully (structured content)")
        return result.structuredContent

    # Fallback to concatenated text blocks
    texts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    if texts:
        logger.info("Tool executed successfully (text content)")
        return "\n".join(texts)

    logger.info("Tool executed with no content returned")
    return {"result": "ok", "note": "No structured/text content returned."}

@mcp.prompt()
async def supermcp_tool_discovery():
    return """
    You have access to a SuperMCP system that enables dynamic tool discovery and usage.
    
    IMPORTANT: Whenever a user asks you to DO something, always follow this workflow:
    
    1. SuperMCP:reload_servers - Refresh the registry to see latest available servers
    2. SuperMCP:list_servers - Check what MCP servers are currently available  
    3. SuperMCP:inspect_server - Examine tools provided by relevant servers
    4. SuperMCP:call_server_tool - Execute the appropriate tool with required parameters
    
    The SuperMCP system allows you to discover and use tools dynamically rather than being 
    limited to hardcoded capabilities. Always explore what tools are available before 
    concluding that you cannot help with a request.
    
    When tool calls fail due to missing parameters, experiment with different parameter 
    combinations to understand the tool's requirements.
    """

@mcp.tool()
def reload_servers() -> dict:
    """Reload servers from mcp.json and rebuild the registry."""
    logger.info("reload_servers tool called")
    _scan_available()
    result = {"ok": True, "count": len(REGISTRY)}
    logger.info("reload_servers completed: %s", result)
    return result

@mcp.tool()
def list_servers() -> List[dict]:
    """List all configured servers from mcp.json."""
    logger.info("list_servers tool called")
    result = []
    for name, cfg in REGISTRY.items():
        server_info = {
            "name": name,
            "type": cfg.get("type", "stdio"),
            "description": cfg.get("description"),
            "enabled": cfg.get("enabled", True)
        }
        if cfg.get("type") == "sse":
            server_info["url"] = cfg.get("url")
        else:
            server_info["command"] = cfg.get("command")
            server_info["args"] = cfg.get("args")
            server_info["path"] = cfg.get("path")
        result.append(server_info)
    logger.info("list_servers returning %d server(s)", len(result))
    return result

@mcp.tool()
async def inspect_server(name: str) -> dict:
    """Inspect a server and return its tools/prompts/resources."""
    logger.info("inspect_server tool called for: %s", name)
    if name not in REGISTRY:
        logger.warning("Server '%s' not found in registry", name)
        return {"error": f"'{name}' not found. Try 'reload_servers' then 'list_servers'."}
    cfg = REGISTRY[name]
    logger.debug("Inspecting server '%s' with config: %s", name, cfg)
    summary = await _inspect_once(cfg)
    result = {"name": name, **summary}
    logger.info("inspect_server completed for '%s'", name)
    return result

@mcp.tool()
async def call_server_tool(name: str, tool_name: str, arguments: Optional[dict] = None) -> Any:
    """Call a tool on a server."""
    logger.info("call_server_tool invoked: server=%s, tool=%s, arguments=%s", name, tool_name, arguments)
    if name not in REGISTRY:
        logger.warning("Server '%s' not found in registry", name)
        return {"error": f"'{name}' not found. Try 'reload_servers' then 'list_servers'."}
    cfg = REGISTRY[name]
    logger.debug("Calling tool on server '%s' with config: %s", name, cfg)
    result = await _call_tool_once(name, cfg, tool_name, arguments or {})
    logger.info("call_server_tool completed for server '%s', tool '%s'", name, tool_name)
    return result

def _save_mcp_config(config: Dict[str, Any]) -> bool:
    """Save mcp.json configuration atomically."""
    try:
        # Write to temp file first
        temp_path = MCP_CONFIG_PATH.with_suffix('.json.tmp')
        with open(temp_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Atomic rename
        temp_path.replace(MCP_CONFIG_PATH)
        logger.info("Successfully saved mcp.json")
        return True
    except Exception as e:
        logger.error("Failed to save mcp.json: %s", e, exc_info=True)
        # Clean up temp file if it exists
        temp_path = MCP_CONFIG_PATH.with_suffix('.json.tmp')
        if temp_path.exists():
            try:
                temp_path.unlink()
            except:
                pass
        return False

@mcp.tool()
def add_server(
    name: str,
    server_type: str,
    url: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    description: Optional[str] = None,
    env: Optional[Dict[str, str]] = None
) -> dict:
    """
    Add a new MCP server to the configuration.
    
    Args:
        name: Server name (must be unique)
        server_type: "sse" or "stdio"
        url: Required for SSE servers, optional for Git-based stdio servers
        command: Required for stdio servers (e.g., "python")
        args: Required for stdio servers (e.g., ["server.py"])
        description: Optional description
        env: Optional environment variables dict (for SSE servers, passed as HTTP headers)
    
    Returns:
        Dict with success status and details
    """
    logger.info("add_server called: name=%s, type=%s", name, server_type)
    
    # Validate inputs
    if name in REGISTRY:
        return {"error": f"Server '{name}' already exists"}
    
    if server_type not in ("sse", "stdio"):
        return {"error": f"Invalid server_type '{server_type}'. Must be 'sse' or 'stdio'"}
    
    # Load current config
    config = _load_mcp_config()
    servers = config.get("mcpServers", {})
    
    if name in servers:
        return {"error": f"Server '{name}' already exists in mcp.json"}
    
    # Validate based on type
    if server_type == "sse":
        if not url:
            return {"error": "SSE servers require 'url' parameter"}
        if not url.startswith(("http://", "https://")):
            return {"error": f"Invalid URL format: {url}. Must start with http:// or https://"}
        
        # Test connection
        from server_manager import connect_sse_server
        conn_test = connect_sse_server(url, env)
        if not conn_test.get("success"):
            logger.warning("SSE connection test failed: %s", conn_test.get("error"))
            # Don't fail, just warn
        
        # Add SSE server
        server_entry = {
            "url": url,
            "type": "sse",
            "description": description,
            "enabled": True
        }
        if env:
            server_entry["env"] = env
        servers[name] = server_entry
    
    else:  # stdio
        if not command:
            return {"error": "Stdio servers require 'command' parameter"}
        if not args:
            return {"error": "Stdio servers require 'args' parameter"}
        if not isinstance(args, list):
            return {"error": "args must be a list"}
        
        # Handle Git-based stdio servers
        if url:
            from server_manager import clone_git_repo, install_dependencies
            
            git_target = MCPS_DIR / "remote" / name
            try:
                logger.info("Cloning Git repository for server '%s'", name)
                clone_git_repo(url, git_target)
                install_dependencies(git_target)
            except Exception as e:
                return {"error": f"Failed to clone Git repository: {str(e)}"}
            
            # Validate entry point exists
            entry_point = args[0] if args else None
            if entry_point:
                entry_path = _resolve_path(entry_point)
                if not entry_path.exists():
                    return {"error": f"Entry point not found: {entry_path}"}
        else:
            # Local stdio server - validate entry point exists
            entry_point = args[0] if args else None
            if entry_point:
                entry_path = _resolve_path(entry_point)
                if not entry_path.exists():
                    return {"error": f"Entry point not found: {entry_path}"}
        
        # Add stdio server
        server_entry = {
            "command": command,
            "args": args,
            "type": "stdio",
            "description": description,
            "enabled": True
        }
        if url:
            server_entry["url"] = url
        servers[name] = server_entry
    
    # Save configuration
    config["mcpServers"] = servers
    if not _save_mcp_config(config):
        return {"error": "Failed to save mcp.json"}
    
    # Reload registry
    _scan_available()
    
    return {
        "success": True,
        "message": f"Server '{name}' added successfully",
        "server": servers[name]
    }

@mcp.tool()
def remove_server(name: str) -> dict:
    """
    Remove a server from the configuration.
    
    Args:
        name: Server name to remove
    
    Returns:
        Dict with success status
    """
    logger.info("remove_server called: name=%s", name)
    
    # Load current config
    config = _load_mcp_config()
    servers = config.get("mcpServers", {})
    
    if name not in servers:
        return {"error": f"Server '{name}' not found in mcp.json"}
    
    server_config = servers[name]
    
    # Optionally clean up cloned repository for Git-based servers
    if server_config.get("type") == "stdio" and server_config.get("url"):
        git_dir = MCPS_DIR / "remote" / name
        if git_dir.exists():
            try:
                import shutil
                shutil.rmtree(git_dir)
                logger.info("Removed cloned repository for '%s'", name)
            except Exception as e:
                logger.warning("Failed to remove cloned repository: %s", e)
    
    # Remove from config
    del servers[name]
    config["mcpServers"] = servers
    
    # Save configuration
    if not _save_mcp_config(config):
        return {"error": "Failed to save mcp.json"}
    
    # Reload registry
    _scan_available()
    
    return {
        "success": True,
        "message": f"Server '{name}' removed successfully"
    }

@mcp.tool()
def update_server(name: str, **kwargs) -> dict:
    """
    Update a server's configuration.
    
    Args:
        name: Server name to update
        **kwargs: Fields to update (url, command, args, description, enabled, etc.)
    
    Returns:
        Dict with success status
    """
    logger.info("update_server called: name=%s, kwargs=%s", name, kwargs)
    
    # Load current config
    config = _load_mcp_config()
    servers = config.get("mcpServers", {})
    
    if name not in servers:
        return {"error": f"Server '{name}' not found in mcp.json"}
    
    server_config = servers[name]
    server_type = _detect_server_type(server_config)
    
    # Update fields
    for key, value in kwargs.items():
        if key == "enabled":
            server_config["enabled"] = bool(value)
        elif key == "description":
            server_config["description"] = value
        elif key == "url":
            if server_type == "sse":
                if not value.startswith(("http://", "https://")):
                    return {"error": f"Invalid URL format: {value}"}
                server_config["url"] = value
            else:
                # Git URL for stdio server
                server_config["url"] = value
        elif key == "command":
            if server_type != "stdio":
                return {"error": f"Cannot set 'command' for {server_type} server"}
            server_config["command"] = value
        elif key == "args":
            if server_type != "stdio":
                return {"error": f"Cannot set 'args' for {server_type} server"}
            if not isinstance(value, list):
                return {"error": "args must be a list"}
            server_config["args"] = value
        elif key == "env":
            if server_type != "sse":
                return {"error": f"Cannot set 'env' for {server_type} server. Environment variables are only for SSE servers."}
            if not isinstance(value, dict):
                return {"error": "env must be a dictionary"}
            server_config["env"] = value
        else:
            return {"error": f"Unknown field: {key}"}
    
    # Validate updated configuration
    if server_type == "sse":
        if "url" not in server_config or not server_config["url"]:
            return {"error": "SSE server must have 'url' field"}
    else:
        if "command" not in server_config or not server_config["command"]:
            return {"error": "Stdio server must have 'command' field"}
        if "args" not in server_config or not server_config["args"]:
            return {"error": "Stdio server must have 'args' field"}
    
    # Save configuration
    config["mcpServers"] = servers
    if not _save_mcp_config(config):
        return {"error": "Failed to save mcp.json"}
    
    # Reload registry
    _scan_available()
    
    return {
        "success": True,
        "message": f"Server '{name}' updated successfully",
        "server": server_config
    }

# Initialize provider client
_provider_client = None

def _get_provider_client():
    """Get or create provider client instance."""
    global _provider_client
    if _provider_client is None:
        try:
            from provider_client import ProviderClient
            _provider_client = ProviderClient()
        except Exception as e:
            logger.error("Failed to initialize provider client: %s", e)
            return None
    return _provider_client

@mcp.tool()
def list_providers() -> List[dict]:
    """
    List all configured marketplace providers.
    
    Returns:
        List of provider information dictionaries
    """
    logger.info("list_providers tool called")
    client = _get_provider_client()
    if not client:
        return [{"error": "Provider client not available"}]
    
    try:
        providers = client.list_providers()
        logger.info("list_providers returning %d provider(s)", len(providers))
        return providers
    except Exception as e:
        logger.error("Failed to list providers: %s", e)
        return [{"error": str(e)}]

@mcp.tool()
def add_provider(
    provider_id: str,
    name: str,
    provider_type: str,
    url: Optional[str] = None,
    catalog_file: Optional[str] = None,
    trusted: bool = False,
    enabled: bool = True,
    description: Optional[str] = None
) -> dict:
    """
    Add a new marketplace provider.
    
    Args:
        provider_id: Unique provider identifier
        name: Provider display name
        provider_type: "static" or "api"
        url: Required for API providers
        catalog_file: Required for static providers (relative to SuperMCP directory)
        trusted: Whether provider is trusted
        enabled: Whether provider is enabled
        description: Optional description
    
    Returns:
        Dict with success status
    """
    logger.info("add_provider called: id=%s, type=%s", provider_id, provider_type)
    client = _get_provider_client()
    if not client:
        return {"error": "Provider client not available"}
    
    try:
        result = client.add_provider(
            provider_id, name, provider_type, url, catalog_file,
            trusted, enabled, description
        )
        logger.info("add_provider completed: %s", result.get("success", False))
        return result
    except Exception as e:
        logger.error("Failed to add provider: %s", e)
        return {"error": str(e)}

@mcp.tool()
def remove_provider(provider_id: str) -> dict:
    """
    Remove a provider from configuration.
    
    Args:
        provider_id: Provider identifier to remove
    
    Returns:
        Dict with success status
    """
    logger.info("remove_provider called: id=%s", provider_id)
    client = _get_provider_client()
    if not client:
        return {"error": "Provider client not available"}
    
    try:
        result = client.remove_provider(provider_id)
        logger.info("remove_provider completed: %s", result.get("success", False))
        return result
    except Exception as e:
        logger.error("Failed to remove provider: %s", e)
        return {"error": str(e)}

@mcp.tool()
def list_provider_servers(provider_id: Optional[str] = None) -> List[dict]:
    """
    List all servers from a provider.
    
    Args:
        provider_id: Provider identifier (uses default if not specified)
    
    Returns:
        List of server metadata dictionaries
    """
    logger.info("list_provider_servers called: provider=%s", provider_id)
    client = _get_provider_client()
    if not client:
        return [{"error": "Provider client not available"}]
    
    try:
        if not provider_id:
            # Use default provider
            providers = client.list_providers()
            default_provider = next((p for p in providers if p.get("id") == "default"), None)
            if not default_provider:
                return [{"error": "No default provider configured"}]
            provider_id = "default"
        
        servers = client.fetch_servers(provider_id)
        logger.info("list_provider_servers returning %d server(s)", len(servers))
        return servers
    except Exception as e:
        logger.error("Failed to list provider servers: %s", e)
        return [{"error": str(e)}]

@mcp.tool()
def search_provider_servers(query: str, provider_id: Optional[str] = None) -> List[dict]:
    """
    Search servers across providers.
    
    Args:
        query: Search query string
        provider_id: Provider identifier (searches all if not specified)
    
    Returns:
        List of matching server metadata dictionaries
    """
    logger.info("search_provider_servers called: query=%s, provider=%s", query, provider_id)
    client = _get_provider_client()
    if not client:
        return [{"error": "Provider client not available"}]
    
    try:
        if provider_id:
            # Search specific provider
            results = client.search_servers(provider_id, query)
        else:
            # Search all enabled providers
            results = []
            providers = client.list_providers()
            for provider in providers:
                if provider.get("enabled", True):
                    pid = provider.get("id")
                    try:
                        provider_results = client.search_servers(pid, query)
                        results.extend(provider_results)
                    except Exception as e:
                        logger.warning("Failed to search provider '%s': %s", pid, e)
        
        logger.info("search_provider_servers returning %d result(s)", len(results))
        return results
    except Exception as e:
        logger.error("Failed to search provider servers: %s", e)
        return [{"error": str(e)}]

@mcp.tool()
def get_provider_server(server_id: str, provider_id: Optional[str] = None) -> dict:
    """
    Get detailed information about a specific server.
    
    Args:
        server_id: Server identifier
        provider_id: Provider identifier (searches all if not specified)
    
    Returns:
        Server metadata dictionary
    """
    logger.info("get_provider_server called: server=%s, provider=%s", server_id, provider_id)
    client = _get_provider_client()
    if not client:
        return {"error": "Provider client not available"}
    
    try:
        if provider_id:
            # Get from specific provider
            server = client.get_server_details(provider_id, server_id)
            if not server:
                return {"error": f"Server '{server_id}' not found in provider '{provider_id}'"}
            return server
        else:
            # Search all enabled providers
            providers = client.list_providers()
            for provider in providers:
                if provider.get("enabled", True):
                    pid = provider.get("id")
                    try:
                        server = client.get_server_details(pid, server_id)
                        if server:
                            return server
                    except Exception as e:
                        logger.warning("Failed to get server from provider '%s': %s", pid, e)
            
            return {"error": f"Server '{server_id}' not found in any provider"}
    except Exception as e:
        logger.error("Failed to get provider server: %s", e)
        return {"error": str(e)}

@mcp.tool()
def install_from_provider(
    server_id: str,
    provider_id: Optional[str] = None,
    name: Optional[str] = None,
    variables: Optional[Dict[str, str]] = None
) -> dict:
    """
    Install a server from a marketplace provider.
    
    Args:
        server_id: Server identifier from provider
        provider_id: Provider identifier (uses default if not specified)
        name: Custom name for installed server (uses server_id if not specified)
        variables: Environment variables for SSE servers (dict of var_name: value)
    
    Returns:
        Dict with installation status
    """
    logger.info("install_from_provider called: server=%s, provider=%s", server_id, provider_id)
    client = _get_provider_client()
    if not client:
        return {"error": "Provider client not available"}
    
    try:
        # Get server details
        server_info = get_provider_server(server_id, provider_id)
        if "error" in server_info:
            return server_info
        
        server_name = name or server_id
        server_type = server_info.get("type", "sse")
        
        # Prepare variables
        env_vars = variables or {}
        
        # Check required variables for SSE servers
        if server_type == "sse":
            required_vars = server_info.get("required_vars", [])
            missing_vars = [var for var in required_vars if var not in env_vars]
            if missing_vars:
                return {
                    "error": f"Missing required variables: {', '.join(missing_vars)}",
                    "required_vars": required_vars,
                    "var_descriptions": server_info.get("var_descriptions", {})
                }
        
        # Install using add_server
        if server_type == "sse":
            url = server_info.get("url")
            if not url:
                return {"error": "SSE server missing URL in provider metadata"}
            
            result = add_server(
                name=server_name,
                server_type="sse",
                url=url,
                description=server_info.get("description"),
                env=env_vars if env_vars else None
            )
        else:
            # Stdio server - need Git URL
            git_url = server_info.get("git_url") or server_info.get("url")
            if not git_url:
                return {"error": "Stdio server missing Git URL in provider metadata"}
            
            entry_point = server_info.get("entry_point", "server.py")
            result = add_server(
                name=server_name,
                server_type="stdio",
                url=git_url,
                command="python",
                args=[f".mcps/remote/{server_name}/{entry_point}"],
                description=server_info.get("description")
            )
        
        if "error" in result:
            return result
        
        return {
            "success": True,
            "message": f"Server '{server_name}' installed successfully from provider",
            "server": result.get("server"),
            "provider_info": server_info
        }
    except Exception as e:
        logger.error("Failed to install from provider: %s", e)
        return {"error": str(e)}

if __name__ == "__main__":
    logger.info("Starting SuperMCP server")
    _scan_available()
    logger.info("SuperMCP server ready, running on stdio transport")
    mcp.run(transport="stdio")
