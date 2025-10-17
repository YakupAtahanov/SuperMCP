import asyncio
import logging
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('testclient.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TestClient")

async def main():
    logger.info("Starting SuperMCP Test Client")
    try:
        params = StdioServerParameters(command="python", args=["SuperMCP.py"])
        logger.debug("Connecting to SuperMCP server")
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                logger.debug("Initializing client session")
                await s.initialize()
                logger.info("Client session initialized successfully")

                print("=== SuperMCP Test Client ===")
                print("Reloading and listing servers...")
                logger.info("Calling reload_servers tool")
                await s.call_tool("reload_servers", {})
                logger.info("Calling list_servers tool")
                servers = await s.call_tool("list_servers", {})
                print("Available servers:", servers.structuredContent or servers.content)
                logger.info("Found servers: %s", servers.structuredContent or servers.content)

                # Test with ShellMCP (the actual available server)
                print("\n=== Testing ShellMCP Server ===")
                logger.info("Inspecting ShellMCP server")
                result = await s.call_tool("inspect_server", {"name": "ShellMCP"})
                print("ShellMCP capabilities:", result.structuredContent or result.content)
                logger.info("ShellMCP capabilities: %s", result.structuredContent or result.content)

                # Call a ShellMCP tool to get platform info
                print("\n=== Calling ShellMCP get_platform_info ===")
                logger.info("Calling get_platform_info tool on ShellMCP")
                call = await s.call_tool("call_server_tool", {
                    "name": "ShellMCP",
                    "tool_name": "get_platform_info",
                    "arguments": {}
                })
                print("Platform info result:", call.structuredContent or call.content)
                logger.info("Platform info result: %s", call.structuredContent or call.content)

        logger.info("Test client completed successfully")
    except Exception as e:
        logger.error("Test client failed: %s", e, exc_info=True)
        raise

if __name__ == "__main__":
    asyncio.run(main())
