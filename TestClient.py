import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="python", args=["SuperMCP.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            print("=== SuperMCP Test Client ===")
            print("Reloading and listing servers...")
            await s.call_tool("reload_servers", {})
            servers = await s.call_tool("list_servers", {})
            print("Available servers:", servers.structuredContent or servers.content)

            # Test with ShellMCP (the actual available server)
            print("\n=== Testing ShellMCP Server ===")
            result = await s.call_tool("inspect_server", {"name": "ShellMCP"})
            print("ShellMCP capabilities:", result.structuredContent or result.content)

            # Call a ShellMCP tool to get platform info
            print("\n=== Calling ShellMCP get_platform_info ===")
            call = await s.call_tool("call_server_tool", {
                "name": "ShellMCP",
                "tool_name": "get_platform_info",
                "arguments": {}
            })
            print("Platform info result:", call.structuredContent or call.content)

if __name__ == "__main__":
    asyncio.run(main())
