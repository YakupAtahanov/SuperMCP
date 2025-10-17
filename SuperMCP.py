import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from mcp.server.fastmcp import FastMCP
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

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
MCPS_DIR = HERE / "available_mcps"

mcp = FastMCP("SuperMCP")
logger.info("SuperMCP initialized with MCPS_DIR: %s", MCPS_DIR)

# name -> { "command": str, "args": List[str], "path": str }
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

def _scan_available():
    logger.info("Starting scan of available MCP servers in: %s", MCPS_DIR)
    REGISTRY.clear()
    if not MCPS_DIR.exists():
        logger.warning("MCPS_DIR does not exist, creating: %s", MCPS_DIR)
        MCPS_DIR.mkdir(parents=True, exist_ok=True)
    # Recursively find all server.py files in subdirectories
    found_count = 0
    for f in sorted(MCPS_DIR.rglob("server.py")):
        if _is_server_file(f):
            name = _derive_name(f)
            # Launch via stdio: python <script>
            REGISTRY[name] = {
                "command": _python_cmd(),
                "args": [str(f)],
                "path": str(f),
            }
            logger.info("Registered MCP server: %s at %s", name, f)
            found_count += 1
    logger.info("Scan complete. Found %d MCP server(s): %s", found_count, list(REGISTRY.keys()))

async def _inspect_once(command: str, args: List[str]) -> Dict[str, Any]:
    logger.debug("Inspecting server: command=%s, args=%s", command, args)
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
                logger.info("Inspection successful: %d tools, %d prompts, %d resources", 
                           len(result["tools"]), len(result["prompts"]), len(result["resources"]))
                return result
    except Exception as e:
        logger.error("Failed to inspect server: %s", e, exc_info=True)
        raise

async def _call_tool_once(command: str, args: List[str], tool_name: str, arguments: dict) -> Any:
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

                logger.debug("Executing tool '%s' on remote server", tool_name)
                result = await session.call_tool(tool_name, arguments or {})

                # Prefer structured content
                if getattr(result, "structuredContent", None) is not None:
                    logger.info("Tool '%s' executed successfully (structured content)", tool_name)
                    return result.structuredContent

                # Fallback to concatenated text blocks
                texts = []
                for block in getattr(result, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        texts.append(text)
                if texts:
                    logger.info("Tool '%s' executed successfully (text content)", tool_name)
                    return "\n".join(texts)

                logger.info("Tool '%s' executed with no content returned", tool_name)
                return {"result": "ok", "note": "No structured/text content returned."}
    except Exception as e:
        logger.error("Failed to call tool '%s': %s", tool_name, e, exc_info=True)
        raise

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
    """Rescan /available_mcps and rebuild the registry."""
    logger.info("reload_servers tool called")
    _scan_available()
    result = {"ok": True, "count": len(REGISTRY)}
    logger.info("reload_servers completed: %s", result)
    return result

@mcp.tool()
def list_servers() -> List[dict]:
    """List auto-detected servers."""
    logger.info("list_servers tool called")
    result = [{"name": k, "path": v["path"], "command": v["command"], "args": v["args"]} for k, v in REGISTRY.items()]
    logger.info("list_servers returning %d server(s)", len(result))
    return result

@mcp.tool()
async def inspect_server(name: str) -> dict:
    """Launch a server once and return its tools/prompts/resources."""
    logger.info("inspect_server tool called for: %s", name)
    if name not in REGISTRY:
        logger.warning("Server '%s' not found in registry", name)
        return {"error": f"'{name}' not found. Try 'reload_servers' then 'list_servers'."}
    cfg = REGISTRY[name]
    logger.debug("Inspecting server '%s' with config: %s", name, cfg)
    summary = await _inspect_once(cfg["command"], cfg["args"])
    result = {"name": name, **summary}
    logger.info("inspect_server completed for '%s'", name)
    return result

@mcp.tool()
async def call_server_tool(name: str, tool_name: str, arguments: Optional[dict] = None) -> Any:
    """Launch a server once and call one of its tools."""
    logger.info("call_server_tool invoked: server=%s, tool=%s, arguments=%s", name, tool_name, arguments)
    if name not in REGISTRY:
        logger.warning("Server '%s' not found in registry", name)
        return {"error": f"'{name}' not found. Try 'reload_servers' then 'list_servers'."}
    cfg = REGISTRY[name]
    logger.debug("Calling tool on server '%s' with config: %s", name, cfg)
    result = await _call_tool_once(cfg["command"], cfg["args"], tool_name, arguments or {})
    logger.info("call_server_tool completed for server '%s', tool '%s'", name, tool_name)
    return result

if __name__ == "__main__":
    logger.info("Starting SuperMCP server")
    _scan_available()
    logger.info("SuperMCP server ready, running on stdio transport")
    mcp.run(transport="stdio")
