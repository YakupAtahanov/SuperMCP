"""
SuperMCP — MCP orchestration layer.

Reads a registry path from the SUPERMCP_REGISTRY environment variable (or
a .env file), then loads and manages MCP servers defined in that registry
file.  The registry (and any relative paths it contains) can live anywhere
on the system.
"""

import sys
import os
import json
import logging
import atexit
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
    pass


# =============================================================================
# Logging
# =============================================================================

HERE = Path(__file__).resolve().parent
_log_file = HERE / "supermcp.log"

_log_handlers: list = [logging.FileHandler(_log_file)]
if os.environ.get("SUPERMCP_DEBUG"):
    _log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("SuperMCP")


# =============================================================================
# Configuration  (env var / .env → SUPERMCP_REGISTRY → actual server list)
# =============================================================================


def _parse_dotenv(path: Path) -> Dict[str, str]:
    """Parse a simple .env file (KEY=VALUE lines, # comments)."""
    result: Dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def _resolve_registry() -> Dict[str, Any]:
    """
    Determine the registry path from environment or ``.env`` file.

    Lookup order:
        1. ``SUPERMCP_REGISTRY`` environment variable.
        2. ``SUPERMCP_REGISTRY`` in ``.env`` next to ``SuperMCP.py``.

    Relative paths are resolved from the SuperMCP directory.
    """
    raw = os.environ.get("SUPERMCP_REGISTRY", "")

    if not raw:
        dotenv = _parse_dotenv(HERE / ".env")
        raw = dotenv.get("SUPERMCP_REGISTRY", "")

    if not raw:
        logger.warning(
            "SUPERMCP_REGISTRY not set — configure it in .env or as an environment variable"
        )
        return {"registry_path": None, "registry_dir": None}

    p = Path(raw)
    registry_path = p.resolve() if p.is_absolute() else (HERE / raw).resolve()

    if not registry_path.exists():
        logger.warning("Registry file not found yet: %s", registry_path)

    return {
        "registry_path": registry_path,
        "registry_dir": registry_path.parent,
    }


_cfg = _resolve_registry()
REGISTRY_PATH: Optional[Path] = _cfg["registry_path"]
REGISTRY_DIR: Optional[Path] = _cfg["registry_dir"]

logger.info("SuperMCP starting — registry: %s", REGISTRY_PATH)


# =============================================================================
# FastMCP instance & in-memory registry
# =============================================================================

mcp = FastMCP("SuperMCP")

REGISTRY: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# Persistent sub-server cache
# =============================================================================

class CachedSubServer:
    """Keep a stdio sub-server process alive for fast repeated calls."""

    def __init__(self, name: str, process, tools: List[str]):
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
        if not self.is_alive():
            return None
        try:
            msg = json.dumps(request) + "\n"
            self.process.stdin.write(msg.encode())
            self.process.stdin.flush()
            line = self.process.stdout.readline().decode().strip()
            if line:
                return json.loads(line)
        except Exception as e:
            logger.error("CachedSubServer %s send_recv failed: %s", self.name, e)
        return None

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        if not self.is_alive():
            return {"error": f"Server {self.name} is not running"}
        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not found. Available: {self.tools}"}
        resp = self.send_recv({
            "jsonrpc": "2.0",
            "id": self.next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        })
        if not resp:
            return {"error": "Empty response from server"}
        if "error" in resp:
            return {"error": resp["error"]}
        return resp.get("result", {})

    def disconnect(self):
        if self.process:
            logger.info("Disconnecting cached sub-server: %s", self.name)
            try:
                self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None


_cached_subserver: Optional[CachedSubServer] = None


def _get_or_create_cached_subserver(
    server_name: str, command: str, args: List[str],
) -> Optional[CachedSubServer]:
    """Return a cached sub-server, creating (or replacing) one if needed."""
    global _cached_subserver

    if _cached_subserver is not None:
        if _cached_subserver.name == server_name and _cached_subserver.is_alive():
            return _cached_subserver
        _cached_subserver.disconnect()
        _cached_subserver = None

    logger.info("Starting cached sub-server: %s", server_name)
    try:
        import subprocess

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        cwd = str(REGISTRY_DIR) if REGISTRY_DIR else str(HERE)

        process = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            cwd=cwd,
            creationflags=creationflags,
        )

        req_id = [0]

        def _next_id():
            req_id[0] += 1
            return req_id[0]

        def _send_recv(request):
            msg = json.dumps(request) + "\n"
            process.stdin.write(msg.encode())
            process.stdin.flush()
            line = process.stdout.readline().decode().strip()
            return json.loads(line) if line else None

        # Initialise
        init_resp = _send_recv({
            "jsonrpc": "2.0",
            "id": _next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "SuperMCP", "version": "1.0"},
            },
        })
        if not init_resp or "error" in init_resp:
            raise RuntimeError(f"Failed to initialise: {init_resp}")

        process.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()
        )
        process.stdin.flush()

        # Discover tools
        tools_resp = _send_recv({
            "jsonrpc": "2.0",
            "id": _next_id(),
            "method": "tools/list",
        })
        available_tools = []
        if tools_resp and "result" in tools_resp:
            available_tools = [t["name"] for t in tools_resp["result"].get("tools", [])]

        cached = CachedSubServer(server_name, process, available_tools)
        cached._request_id = req_id[0]
        _cached_subserver = cached
        logger.info("Cached sub-server %s ready with %d tools", server_name, len(available_tools))
        return cached

    except Exception as e:
        logger.error("Failed to start cached sub-server %s: %s", server_name, e)
        return None


def _disconnect_cached_subserver():
    global _cached_subserver
    if _cached_subserver:
        _cached_subserver.disconnect()
        _cached_subserver = None


atexit.register(_disconnect_cached_subserver)


# =============================================================================
# Helpers
# =============================================================================

def _check_registry() -> Optional[dict]:
    """Return an error dict when the registry is not configured, else ``None``."""
    if not REGISTRY_PATH:
        return {"error": "SUPERMCP_REGISTRY not set. Configure it in .env or as an environment variable."}
    return None


def _resolve_path(path_str: str) -> Path:
    """Resolve a path relative to the *registry* directory (or absolute)."""
    p = Path(path_str)
    if p.is_absolute():
        return p
    base = REGISTRY_DIR if REGISTRY_DIR else HERE
    return (base / path_str).resolve()


def _detect_server_type(server_config: Dict[str, Any]) -> str:
    if "type" in server_config:
        return server_config["type"]
    has_url = bool(server_config.get("url"))
    has_cmd = bool(server_config.get("command"))
    has_args = bool(server_config.get("args"))
    if has_cmd and has_args:
        return "stdio"
    if has_url and not (has_cmd or has_args):
        return "sse"
    return "stdio"


def _create_sse_headers(env: Optional[Dict[str, str]]) -> Dict[str, str]:
    if not env:
        return {}
    return {f"X-MCP-{k.upper().replace('_', '-')}": v for k, v in env.items()}


def _mask_env(env: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if not env:
        return None
    return {k: "***" for k in env}


# =============================================================================
# Registry loading / saving
# =============================================================================

def _load_registry() -> Dict[str, Any]:
    """Load the server registry JSON pointed to by ``REGISTRY_PATH``."""
    if not REGISTRY_PATH:
        return {"mcpServers": {}}
    if not REGISTRY_PATH.exists():
        logger.warning("Registry file not found: %s — creating empty one", REGISTRY_PATH)
        empty: Dict[str, Any] = {"mcpServers": {}}
        try:
            REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(REGISTRY_PATH, "w") as f:
                json.dump(empty, f, indent=2)
        except Exception as e:
            logger.error("Failed to create registry file: %s", e)
        return empty
    try:
        with open(REGISTRY_PATH, "r") as f:
            data = json.load(f)
        if "mcpServers" not in data:
            data["mcpServers"] = {}
        return data
    except Exception as e:
        logger.error("Failed to load registry: %s", e)
        return {"mcpServers": {}}


def _save_registry(config: Dict[str, Any]) -> bool:
    """Save the server registry atomically."""
    if not REGISTRY_PATH:
        logger.error("Cannot save — registryPath not configured")
        return False
    try:
        tmp = REGISTRY_PATH.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
        tmp.replace(REGISTRY_PATH)
        logger.info("Registry saved to %s", REGISTRY_PATH)
        return True
    except Exception as e:
        logger.error("Failed to save registry: %s", e)
        tmp = REGISTRY_PATH.with_suffix(".json.tmp")
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return False


def _scan_available():
    """Populate ``REGISTRY`` from the registry file."""
    logger.info("Scanning registry at %s", REGISTRY_PATH)
    REGISTRY.clear()

    if _check_registry():
        logger.warning("Registry not configured — skipping scan")
        return

    config = _load_registry()
    servers = config.get("mcpServers", {})
    if not servers:
        logger.info("No servers in registry")
        return

    mcps_dir = (REGISTRY_DIR / ".mcps") if REGISTRY_DIR else (HERE / ".mcps")
    count = 0

    for name, sc in servers.items():
        if not sc.get("enabled", True):
            continue

        stype = _detect_server_type(sc)

        # -- SSE server --
        if stype == "sse":
            if not sc.get("url"):
                logger.error("SSE server '%s' missing 'url'", name)
                continue
            REGISTRY[name] = {
                "type": "sse",
                "url": sc["url"],
                "command": None,
                "args": None,
                "path": None,
                "description": sc.get("description"),
                "enabled": True,
                "env": sc.get("env"),
            }
            count += 1
            continue

        # -- stdio server --
        if not sc.get("command") or not sc.get("args"):
            logger.error("Stdio server '%s' missing command/args", name)
            continue

        # Git-based: clone if the repo isn't there yet
        if sc.get("url"):
            from server_manager import clone_git_repo, install_dependencies

            git_target = mcps_dir / "remote" / name
            if not git_target.exists():
                try:
                    clone_git_repo(sc["url"], git_target)
                    install_dependencies(git_target)
                except Exception as e:
                    logger.error("Git clone failed for '%s': %s", name, e)
                    continue

        # Validate entry point
        entry = sc["args"][0] if sc["args"] else None
        if not entry:
            logger.error("No entry point for server '%s'", name)
            continue
        entry_path = _resolve_path(entry)
        if not entry_path.exists():
            logger.error("Entry point not found for '%s': %s", name, entry_path)
            continue

        REGISTRY[name] = {
            "type": "stdio",
            "command": sc["command"],
            "args": sc["args"],
            "url": sc.get("url"),
            "path": str(entry_path),
            "description": sc.get("description"),
            "enabled": True,
        }
        count += 1

    logger.info("Scan complete: %d server(s) loaded — %s", count, list(REGISTRY.keys()))


# =============================================================================
# Server inspection & tool calling
# =============================================================================

async def _inspect_once(server_config: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect a server's capabilities (tools, prompts, resources)."""
    stype = server_config.get("type", "stdio")

    if stype == "sse":
        url = server_config.get("url")
        if not url:
            raise ValueError("SSE server missing URL")

        env = server_config.get("env")
        headers = _create_sse_headers(env)

        try:
            if SSE_AVAILABLE:
                if headers:
                    import httpx
                    from httpx_sse import EventSource

                    async with httpx.AsyncClient() as client:
                        async with EventSource(url, client=client, headers=headers) as es:
                            async for _event in es:
                                pass
                            return {
                                "tools": [], "prompts": [], "resources": [],
                                "note": "SSE with headers — partial inspection",
                            }
                else:
                    async with sse_client(url) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        prompts = await session.list_prompts()
                        resources = await session.list_resources()
                        return {
                            "tools": [t.name for t in getattr(tools, "tools", [])],
                            "prompts": [p.name for p in getattr(prompts, "prompts", [])],
                            "resources": [r.uri for r in getattr(resources, "resources", [])],
                        }
            else:
                import httpx

                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, headers=headers, timeout=5.0)
                    return {
                        "tools": [], "prompts": [], "resources": [],
                        "note": "SSE client not available",
                        "status_code": resp.status_code,
                    }
        except Exception as e:
            logger.error("SSE inspection failed: %s", e, exc_info=True)
            raise

    # stdio
    command = server_config.get("command")
    args = server_config.get("args")
    if not command or not args:
        raise ValueError("Stdio server missing command or args")

    try:
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
    except Exception as e:
        logger.error("Stdio inspection failed: %s", e, exc_info=True)
        raise


def _call_stdio_tool_cached(
    server_name: str, command: str, args: List[str],
    tool_name: str, arguments: dict,
) -> Any:
    """Call a tool via the cached persistent sub-server connection."""
    cached = _get_or_create_cached_subserver(server_name, command, args)
    if cached is None:
        return {"error": f"Failed to connect to server {server_name}"}

    result = cached.call_tool(tool_name, arguments or {})

    if isinstance(result, dict):
        if "error" in result:
            return result
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        content = result.get("content", [])
        if content:
            texts = [item.get("text", "") for item in content if isinstance(item, dict)]
            if texts:
                return "\n".join(texts)
    return result


async def _call_tool_once(
    server_name: str, server_config: Dict[str, Any],
    tool_name: str, arguments: dict,
) -> Any:
    """Call a tool on a server (SSE or stdio)."""
    stype = server_config.get("type", "stdio")

    if stype == "sse":
        url = server_config.get("url")
        if not url:
            raise ValueError("SSE server missing URL")

        env = server_config.get("env")
        headers = _create_sse_headers(env)

        try:
            if SSE_AVAILABLE:
                if headers:
                    return {"error": "SSE with custom headers not fully implemented yet."}
                async with sse_client(url) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = [t.name for t in getattr(tools, "tools", [])]
                    if tool_name not in names:
                        return {"error": f"Tool '{tool_name}' not found. Available: {names}"}
                    result = await session.call_tool(tool_name, arguments or {})
                    return _extract_result_content(result)
            else:
                return {"error": "SSE client not available."}
        except Exception as e:
            logger.error("SSE tool call failed: %s", e, exc_info=True)
            raise

    # stdio
    command = server_config.get("command")
    args = server_config.get("args")
    if not command or not args:
        raise ValueError("Stdio server missing command or args")
    return _call_stdio_tool_cached(server_name, command, args, tool_name, arguments)


def _extract_result_content(result) -> Any:
    """Pull text or structured content out of an MCP result object."""
    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent
    texts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    if texts:
        return "\n".join(texts)
    return {"result": "ok", "note": "No content returned."}


# =============================================================================
# MCP tools exposed to the AI client
# =============================================================================

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
    """


@mcp.tool()
def reload_servers() -> dict:
    """Reload servers from the registry and rebuild the in-memory registry."""
    err = _check_registry()
    if err:
        return err
    _scan_available()
    return {"ok": True, "count": len(REGISTRY), "registry": str(REGISTRY_PATH)}


@mcp.tool()
def list_servers() -> List[dict]:
    """List all registered MCP servers."""
    result = []
    for name, cfg in REGISTRY.items():
        info: Dict[str, Any] = {
            "name": name,
            "type": cfg.get("type", "stdio"),
            "description": cfg.get("description"),
            "enabled": cfg.get("enabled", True),
        }
        if cfg.get("type") == "sse":
            info["url"] = cfg.get("url")
        else:
            info["command"] = cfg.get("command")
            info["args"] = cfg.get("args")
            info["path"] = cfg.get("path")
        result.append(info)
    return result


@mcp.tool()
async def inspect_server(name: str) -> dict:
    """Inspect a server and return its tools / prompts / resources."""
    if name not in REGISTRY:
        return {"error": f"'{name}' not found. Try reload_servers then list_servers."}
    return {"name": name, **(await _inspect_once(REGISTRY[name]))}


@mcp.tool()
async def call_server_tool(
    name: str, tool_name: str, arguments: Optional[dict] = None,
) -> Any:
    """Call a tool on a registered MCP server."""
    if name not in REGISTRY:
        return {"error": f"'{name}' not found. Try reload_servers then list_servers."}
    return await _call_tool_once(name, REGISTRY[name], tool_name, arguments or {})


@mcp.tool()
def add_server(
    name: str,
    server_type: str,
    url: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    description: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> dict:
    """
    Add a new MCP server to the registry.

    Args:
        name:        Unique server name.
        server_type: ``"sse"`` or ``"stdio"``.
        url:         Required for SSE; optional Git URL for stdio.
        command:     Required for stdio (e.g. ``"python"``).
        args:        Required for stdio (e.g. ``["server.py"]``).
        description: Optional human-readable description.
        env:         Optional env-var dict for SSE servers (sent as HTTP headers).
    """
    err = _check_registry()
    if err:
        return err

    if name in REGISTRY:
        return {"error": f"Server '{name}' already exists"}
    if server_type not in ("sse", "stdio"):
        return {"error": f"Invalid server_type '{server_type}'. Must be 'sse' or 'stdio'"}

    config = _load_registry()
    servers = config.get("mcpServers", {})
    if name in servers:
        return {"error": f"Server '{name}' already in registry"}

    mcps_dir = (REGISTRY_DIR / ".mcps") if REGISTRY_DIR else (HERE / ".mcps")

    if server_type == "sse":
        if not url:
            return {"error": "SSE servers require 'url'"}
        if not url.startswith(("http://", "https://")):
            return {"error": f"Invalid URL: {url}"}
        from server_manager import connect_sse_server

        connect_sse_server(url, env)  # best-effort connection test
        entry: Dict[str, Any] = {
            "url": url, "type": "sse",
            "description": description, "enabled": True,
        }
        if env:
            entry["env"] = env
        servers[name] = entry

    else:  # stdio
        if not command:
            return {"error": "Stdio servers require 'command'"}
        if not args or not isinstance(args, list):
            return {"error": "Stdio servers require 'args' (list)"}

        # Git-based server: clone first
        if url:
            from server_manager import clone_git_repo, install_dependencies

            git_target = mcps_dir / "remote" / name
            try:
                clone_git_repo(url, git_target)
                install_dependencies(git_target)
            except Exception as e:
                return {"error": f"Git clone failed: {e}"}

        # Validate entry point
        ep = args[0] if args else None
        if ep:
            ep_path = _resolve_path(ep)
            if not ep_path.exists():
                return {"error": f"Entry point not found: {ep_path}"}

        entry = {
            "command": command, "args": args, "type": "stdio",
            "description": description, "enabled": True,
        }
        if url:
            entry["url"] = url
        servers[name] = entry

    config["mcpServers"] = servers
    if not _save_registry(config):
        return {"error": "Failed to save registry"}
    _scan_available()
    return {"success": True, "message": f"Server '{name}' added", "server": servers[name]}


@mcp.tool()
def remove_server(name: str) -> dict:
    """Remove a server from the registry."""
    err = _check_registry()
    if err:
        return err

    config = _load_registry()
    servers = config.get("mcpServers", {})
    if name not in servers:
        return {"error": f"Server '{name}' not found in registry"}

    sc = servers[name]
    # Clean up cloned repos for Git-based servers
    if sc.get("type") == "stdio" and sc.get("url"):
        mcps_dir = (REGISTRY_DIR / ".mcps") if REGISTRY_DIR else (HERE / ".mcps")
        git_dir = mcps_dir / "remote" / name
        if git_dir.exists():
            import shutil

            try:
                shutil.rmtree(git_dir)
            except Exception as e:
                logger.warning("Failed to remove cloned repo: %s", e)

    del servers[name]
    config["mcpServers"] = servers
    if not _save_registry(config):
        return {"error": "Failed to save registry"}
    _scan_available()
    return {"success": True, "message": f"Server '{name}' removed"}


@mcp.tool()
def update_server(name: str, **kwargs) -> dict:
    """Update a server's configuration in the registry."""
    err = _check_registry()
    if err:
        return err

    config = _load_registry()
    servers = config.get("mcpServers", {})
    if name not in servers:
        return {"error": f"Server '{name}' not found in registry"}

    sc = servers[name]
    st = _detect_server_type(sc)

    for key, value in kwargs.items():
        if key == "enabled":
            sc["enabled"] = bool(value)
        elif key == "description":
            sc["description"] = value
        elif key == "url":
            if st == "sse" and not value.startswith(("http://", "https://")):
                return {"error": f"Invalid URL: {value}"}
            sc["url"] = value
        elif key == "command":
            if st != "stdio":
                return {"error": f"Cannot set 'command' on {st} server"}
            sc["command"] = value
        elif key == "args":
            if st != "stdio":
                return {"error": f"Cannot set 'args' on {st} server"}
            if not isinstance(value, list):
                return {"error": "args must be a list"}
            sc["args"] = value
        elif key == "env":
            if st != "sse":
                return {"error": "env is only for SSE servers"}
            if not isinstance(value, dict):
                return {"error": "env must be a dict"}
            sc["env"] = value
        else:
            return {"error": f"Unknown field: {key}"}

    config["mcpServers"] = servers
    if not _save_registry(config):
        return {"error": "Failed to save registry"}
    _scan_available()
    return {"success": True, "message": f"Server '{name}' updated", "server": sc}


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    logger.info("Starting SuperMCP server")
    _scan_available()
    logger.info("SuperMCP ready — registry: %s", REGISTRY_PATH)
    mcp.run(transport="stdio")
