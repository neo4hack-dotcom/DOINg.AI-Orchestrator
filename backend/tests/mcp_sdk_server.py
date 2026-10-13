"""An MCP server built with the official Python SDK (interop test). Usage: stdio | http <port>."""
import sys

try:
    from mcp.server.mcpserver import MCPServer as Server  # SDK 2.x
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as Server

from mcp.types import ToolAnnotations

server = Server("sdk-test", instructions="Official SDK test server.")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


@server.tool()
def shout(text: str) -> str:
    """Upper-case a text."""
    return text.upper()


@server.resource("memo://greeting")
def greeting() -> str:
    """A greeting."""
    return "hello from the sdk"


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "http":
        import functools

        import anyio

        anyio.run(functools.partial(server.run_streamable_http_async, port=int(sys.argv[2])))
    else:
        server.run("stdio")
