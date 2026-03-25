"""Web / documentation fetch tool for the coding-tools MCP server."""

import httpx
from bs4 import BeautifulSoup

MAX_CONTENT_LENGTH = 50_000


def register_web_tools(mcp):
    @mcp.tool()
    async def fetch_url(url: str) -> str:
        """Fetch a URL and return its text content (HTML converted to plain text)."""
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "coding-tools-mcp/1.0"},
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" in content_type:
                soup = BeautifulSoup(response.text, "lxml")
                # Remove script/style noise
                for tag in soup(["script", "style", "nav", "footer"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
            else:
                text = response.text
        if len(text) > MAX_CONTENT_LENGTH:
            text = text[:MAX_CONTENT_LENGTH] + "\n... (truncated)"
        return text
