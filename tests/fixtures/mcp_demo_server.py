"""A tiny MCP server for the tests (stdio, or streamable HTTP with --http PORT --token T)."""
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

s = MCPServer("demo", instructions="Demo server for tests.")


@s.tool()
def add(a: int, b: int) -> int:
    "Add two numbers"
    return a + b


@s.tool()
def shout(text: str) -> str:
    "Upper-case a text"
    return text.upper()


@s.tool()
def fail(msg: str) -> str:
    "Always fails"
    raise ToolError(msg)


@s.resource("note://hello")
def hello() -> str:
    "A greeting"
    return "hi there"


if __name__ == "__main__":
    if "--http" in sys.argv:
        import uvicorn
        port = int(sys.argv[sys.argv.index("--http") + 1])
        token = sys.argv[sys.argv.index("--token") + 1]
        app = s.streamable_http_app()

        async def guarded(scope, receive, send):
            if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != f"Bearer {token}".encode():
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"no"})
                return
            await app(scope, receive, send)
        uvicorn.run(guarded, host="127.0.0.1", port=port, log_level="error")
    else:
        s.run()
