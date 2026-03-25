"""Coding Tools MCP Server — FastMCP entrypoint."""

from fastmcp import FastMCP
from tools.files import register_file_tools
from tools.search import register_search_tools
from tools.terminal import register_terminal_tools
from tools.web import register_web_tools

mcp = FastMCP(
    name="coding-tools-mcp",
    instructions=(
        "File, search, git, shell, and web tools for a coding agent operating "
        "on a per-request workspace cloned from a Git repository."
    ),
)

register_file_tools(mcp)
register_search_tools(mcp)
register_terminal_tools(mcp)
register_web_tools(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health():
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok"})


def main():
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
