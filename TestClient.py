import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="python", args=["SuperMCP.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            print("Reloading and listing servers...")
            await s.call_tool("reload_servers", {})
            servers = await s.call_tool("list_servers", {})
            print("Servers:", servers.structuredContent or servers.content)

            # If hello_server.py exists, inspect and call it:
            result = await s.call_tool("inspect_server", {"name": "hello_server"})
            print("Inspect:", result.structuredContent or result.content)

            # Call a known tool on the hello server
            call = await s.call_tool("call_server_tool", {
                "name": "hello_server",
                "tool_name": "say_hello",
                "arguments": {"name": "Yakup"}
            })
            print("say_hello ->", call.structuredContent or call.content)

if __name__ == "__main__":
    asyncio.run(main())
