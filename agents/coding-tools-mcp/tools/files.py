"""File operation tools for the coding-tools MCP server."""

import os
import shutil
import uuid
from pathlib import Path

WORKSPACE_ROOT = Path("/workspace")


def _workspace_path(workspace_id: str, relative_path: str = "") -> Path:
    """Resolve a path within a workspace, guarding against path traversal."""
    base = WORKSPACE_ROOT / workspace_id
    if not base.exists():
        raise ValueError(f"Workspace '{workspace_id}' does not exist")
    if relative_path:
        resolved = (base / relative_path).resolve()
        if not str(resolved).startswith(str(base.resolve())):
            raise ValueError("Path traversal detected")
        return resolved
    return base


def register_file_tools(mcp):
    @mcp.tool()
    async def workspace_init(repo_url: str, branch: str = "main") -> str:
        """Clone a Git repository into a new workspace. Returns the workspace_id."""
        import asyncio
        workspace_id = str(uuid.uuid4())
        dest = WORKSPACE_ROOT / workspace_id
        dest.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--branch", branch, "--depth", "1", repo_url, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(f"git clone failed: {stderr.decode()}")
        return workspace_id

    @mcp.tool()
    async def read_file(workspace_id: str, path: str) -> str:
        """Read file contents from the workspace."""
        file_path = _workspace_path(workspace_id, path)
        return file_path.read_text(encoding="utf-8")

    @mcp.tool()
    async def write_file(workspace_id: str, path: str, content: str) -> str:
        """Create or overwrite a file in the workspace."""
        file_path = _workspace_path(workspace_id, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Written {len(content)} bytes to {path}"

    @mcp.tool()
    async def edit_file(workspace_id: str, path: str, old_text: str, new_text: str) -> str:
        """Find and replace an exact occurrence of old_text with new_text in a file."""
        file_path = _workspace_path(workspace_id, path)
        original = file_path.read_text(encoding="utf-8")
        if old_text not in original:
            raise ValueError(f"old_text not found in {path}")
        updated = original.replace(old_text, new_text, 1)
        file_path.write_text(updated, encoding="utf-8")
        return f"Replaced occurrence in {path}"

    @mcp.tool()
    async def rename_file(workspace_id: str, old_path: str, new_path: str) -> str:
        """Rename or move a file within the workspace."""
        src = _workspace_path(workspace_id, old_path)
        dst = _workspace_path(workspace_id, new_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return f"Renamed {old_path} → {new_path}"

    @mcp.tool()
    async def delete_file(workspace_id: str, path: str) -> str:
        """Remove a file from the workspace."""
        file_path = _workspace_path(workspace_id, path)
        file_path.unlink()
        return f"Deleted {path}"

    @mcp.tool()
    async def list_directory(workspace_id: str, path: str = "") -> str:
        """List directory entries with type indicators (file/dir)."""
        dir_path = _workspace_path(workspace_id, path)
        if not dir_path.is_dir():
            raise ValueError(f"{path} is not a directory")
        entries = []
        for entry in sorted(dir_path.iterdir()):
            kind = "dir" if entry.is_dir() else "file"
            entries.append(f"{kind}  {entry.name}")
        return "\n".join(entries) if entries else "(empty)"

    @mcp.tool()
    async def workspace_cleanup(workspace_id: str) -> str:
        """Remove a workspace and all its contents."""
        ws = WORKSPACE_ROOT / workspace_id
        if ws.exists():
            shutil.rmtree(ws)
            return f"Workspace {workspace_id} removed"
        return f"Workspace {workspace_id} not found"
