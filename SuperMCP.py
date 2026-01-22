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

def _is_server_file(p: Path) -> bool:
    return (
        p.is_file()
        and p.name == "server.py"
    )

def _python_cmd() -> str:
    # Cross-platform python command
    return sys.executable or "python"

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
                "enabled": True
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
        
        logger.debug("Inspecting SSE server at: %s", url)
        try:
            # Try to use MCP SSE client if available
            if SSE_AVAILABLE:
                # Use MCP SSE client
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
                    response = await client.get(url, timeout=5.0)
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

async def _call_tool_once(server_config: Dict[str, Any], tool_name: str, arguments: dict) -> Any:
    """
    Call a tool on a server.
    
    Supports both stdio and SSE transport types.
    """
    server_type = server_config.get("type", "stdio")
    
    if server_type == "sse":
        # SSE server tool call
        url = server_config.get("url")
        if not url:
            raise ValueError("SSE server missing URL")
        
        logger.info("Calling tool '%s' on SSE server at %s with arguments: %s", tool_name, url, arguments)
        try:
            if SSE_AVAILABLE:
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
        # Stdio server tool call
        command = server_config.get("command")
        args = server_config.get("args")
        if not command or not args:
            raise ValueError("Stdio server missing command or args")
        
        logger.info("Calling tool '%s' with arguments: %s", tool_name, arguments)
        try:
            params = StdioServerParameters(command=command, args=args)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    logger.debug("Initializing client session for tool call")
                    await session.initialize()

                    tools = await session.list_tools()
                    names = [t.name for t in getattr(tools, "tools", [])]
                    if tool_name not in names:
                        logger.warning("Tool '%s' not found. Available tools: %s", tool_name, names)
                        return {"error": f"Tool '{tool_name}' not found. Available: {names}"}

                    logger.debug("Executing tool '%s' on stdio server", tool_name)
                    result = await session.call_tool(tool_name, arguments or {})
                    return _extract_result_content(result)
        except Exception as e:
            logger.error("Failed to call tool '%s': %s", tool_name, e, exc_info=True)
            raise

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
    result = await _call_tool_once(cfg, tool_name, arguments or {})
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
    description: Optional[str] = None
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
        conn_test = connect_sse_server(url)
        if not conn_test.get("success"):
            logger.warning("SSE connection test failed: %s", conn_test.get("error"))
            # Don't fail, just warn
        
        # Add SSE server
        servers[name] = {
            "url": url,
            "type": "sse",
            "description": description,
            "enabled": True
        }
    
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

if __name__ == "__main__":
    logger.info("Starting SuperMCP server")
    _scan_available()
    logger.info("SuperMCP server ready, running on stdio transport")
    mcp.run(transport="stdio")
