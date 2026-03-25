"""Terminal and Git tools for the coding-tools MCP server."""

import asyncio
from pathlib import Path

WORKSPACE_ROOT = Path("/workspace")
COMMAND_TIMEOUT = 120


def _workspace_base(workspace_id: str) -> Path:
    base = WORKSPACE_ROOT / workspace_id
    if not base.exists():
        raise ValueError(f"Workspace '{workspace_id}' does not exist")
    return base


async def _run(cwd: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=COMMAND_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Command timed out after {COMMAND_TIMEOUT}s: {' '.join(args)}")
    output = stdout.decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed (exit {proc.returncode}):\n{output}")
    return output


def register_terminal_tools(mcp):
    @mcp.tool()
    async def run_command(workspace_id: str, command: str) -> str:
        """Execute a shell command inside the workspace directory. Returns stdout+stderr."""
        cwd = _workspace_base(workspace_id)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=COMMAND_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"Command timed out after {COMMAND_TIMEOUT}s")
        output = stdout.decode(errors="replace")
        return f"exit_code={proc.returncode}\n{output}"

    @mcp.tool()
    async def git_status(workspace_id: str) -> str:
        """Return git working tree status (porcelain format)."""
        cwd = _workspace_base(workspace_id)
        return await _run(cwd, "git", "status", "--porcelain")

    @mcp.tool()
    async def git_diff(workspace_id: str) -> str:
        """Return unified diff of unstaged changes."""
        cwd = _workspace_base(workspace_id)
        proc = await asyncio.create_subprocess_exec(
            "git", "diff",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode(errors="replace") or "(no unstaged changes)"

    @mcp.tool()
    async def git_commit(workspace_id: str, message: str) -> str:
        """Stage all changes and create a commit. Returns the commit hash."""
        cwd = _workspace_base(workspace_id)
        await _run(cwd, "git", "add", ".")
        await _run(cwd, "git", "commit", "-m", message)
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    @mcp.tool()
    async def git_push(workspace_id: str) -> str:
        """Push current branch to origin."""
        cwd = _workspace_base(workspace_id)
        return await _run(cwd, "git", "push", "origin", "HEAD")

    @mcp.tool()
    async def gh_pr_create(workspace_id: str, title: str, body: str) -> str:
        """Create a GitHub pull request. Returns the PR URL."""
        cwd = _workspace_base(workspace_id)
        return await _run(cwd, "gh", "pr", "create", "--title", title, "--body", body)
