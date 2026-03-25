"""Search tools for the coding-tools MCP server."""

import fnmatch
import os
import re
from pathlib import Path

WORKSPACE_ROOT = Path("/workspace")
MAX_RESULTS = 200


def _workspace_base(workspace_id: str) -> Path:
    base = WORKSPACE_ROOT / workspace_id
    if not base.exists():
        raise ValueError(f"Workspace '{workspace_id}' does not exist")
    return base


def register_search_tools(mcp):
    @mcp.tool()
    async def search_files(
        workspace_id: str,
        regex: str,
        path: str = "",
    ) -> str:
        """Search file contents using a regex pattern. Returns matching lines with file:line context."""
        base = _workspace_base(workspace_id)
        search_root = (base / path) if path else base
        pattern = re.compile(regex)
        results = []

        for root, dirs, files in os.walk(search_root):
            # Skip hidden directories (e.g., .git)
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for filename in files:
                filepath = Path(root) / filename
                try:
                    text = filepath.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line):
                        rel = filepath.relative_to(base)
                        results.append(f"{rel}:{lineno}: {line.rstrip()}")
                        if len(results) >= MAX_RESULTS:
                            results.append(f"... (truncated at {MAX_RESULTS} results)")
                            return "\n".join(results)

        return "\n".join(results) if results else "No matches found"

    @mcp.tool()
    async def search_filenames(workspace_id: str, glob_pattern: str) -> str:
        """Find files by name pattern (e.g. '*.py', '*_test.go'). Returns matching paths."""
        base = _workspace_base(workspace_id)
        matches = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for filename in files:
                if fnmatch.fnmatch(filename, glob_pattern):
                    rel = Path(root, filename).relative_to(base)
                    matches.append(str(rel))
        return "\n".join(sorted(matches)) if matches else "No files matched"
