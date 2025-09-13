import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

from mcp.server.fastmcp import FastMCP
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
MCPS_DIR = HERE / "available_mcps"

mcp = FastMCP("SuperMCP")

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
    REGISTRY.clear()
    if not MCPS_DIR.exists():
        MCPS_DIR.mkdir(parents=True, exist_ok=True)
    # Recursively find all server.py files in subdirectories
    for f in sorted(MCPS_DIR.rglob("server.py")):
        if _is_server_file(f):
            name = _derive_name(f)
            # Launch via stdio: python <script>
            REGISTRY[name] = {
                "command": _python_cmd(),
                "args": [str(f)],
                "path": str(f),
            }

async def _inspect_once(command: str, args: List[str]) -> Dict[str, Any]:
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            prompts = await session.list_prompts()
            resources = await session.list_resources()
            return {
                "tools": [t.name for t in getattr(tools, "tools", [])],
                "prompts": [p.name for p in getattr(prompts, "prompts", [])],
                "resources": [r.uri for r in getattr(resources, "resources", [])],
            }

async def _call_tool_once(command: str, args: List[str], tool_name: str, arguments: dict) -> Any:
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in getattr(tools, "tools", [])]
            if tool_name not in names:
                return {"error": f"Tool '{tool_name}' not found. Available: {names}"}

            result = await session.call_tool(tool_name, arguments or {})

            # Prefer structured content
            if getattr(result, "structuredContent", None) is not None:
                return result.structuredContent

            # Fallback to concatenated text blocks
            texts = []
            for block in getattr(result, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    texts.append(text)
            if texts:
                return "\n".join(texts)

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
    """Rescan /available_mcps and rebuild the registry."""
    _scan_available()
    return {"ok": True, "count": len(REGISTRY)}

@mcp.tool()
def list_servers() -> List[dict]:
    """List auto-detected servers."""
    return [{"name": k, "path": v["path"], "command": v["command"], "args": v["args"]} for k, v in REGISTRY.items()]

@mcp.tool()
async def inspect_server(name: str) -> dict:
    """Launch a server once and return its tools/prompts/resources."""
    if name not in REGISTRY:
        return {"error": f"'{name}' not found. Try 'reload_servers' then 'list_servers'."}
    cfg = REGISTRY[name]
    summary = await _inspect_once(cfg["command"], cfg["args"])
    return {"name": name, **summary}

@mcp.tool()
async def call_server_tool(name: str, tool_name: str, arguments: Optional[dict] = None) -> Any:
    """Launch a server once and call one of its tools."""
    if name not in REGISTRY:
        return {"error": f"'{name}' not found. Try 'reload_servers' then 'list_servers'."}
    cfg = REGISTRY[name]
    return await _call_tool_once(cfg["command"], cfg["args"], tool_name, arguments or {})

if __name__ == "__main__":
    _scan_available()
    mcp.run(transport="stdio")
