"""
CloudForge MCP Server — Filesystem
Exposes local repo file operations as MCP tools.
Read files, write generated code, search by pattern, run shell commands.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

log = structlog.get_logger()
server = Server("cloudforge-filesystem")

# Safety: restrict to repo root only
REPO_ROOT = Path(os.getenv("CLOUDFORGE_REPO_ROOT", ".")).resolve()
MAX_FILE_SIZE = 500_000  # 500KB


def _safe_path(rel_path: str) -> Path:
    """Resolve path and ensure it stays within REPO_ROOT."""
    resolved = (REPO_ROOT / rel_path).resolve()
    if not str(resolved).startswith(str(REPO_ROOT)):
        raise PermissionError(f"Path outside repo root: {rel_path}")
    return resolved


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="fs_read_file",
             description="Read a file from the repository",
             inputSchema={"type": "object", "properties": {
                 "path": {"type": "string", "description": "Relative path from repo root"},
             }, "required": ["path"]}),

        Tool(name="fs_write_file",
             description="Write content to a file (creates parent directories if needed)",
             inputSchema={"type": "object", "properties": {
                 "path": {"type": "string"},
                 "content": {"type": "string"},
                 "create_dirs": {"type": "boolean", "default": True},
             }, "required": ["path", "content"]}),

        Tool(name="fs_list_dir",
             description="List files in a directory",
             inputSchema={"type": "object", "properties": {
                 "path": {"type": "string", "default": "."},
                 "recursive": {"type": "boolean", "default": False},
                 "pattern": {"type": "string", "description": "Glob pattern e.g. '*.tf'"},
             }}),

        Tool(name="fs_search_content",
             description="Search file contents for a pattern (grep-like)",
             inputSchema={"type": "object", "properties": {
                 "pattern": {"type": "string"},
                 "path": {"type": "string", "default": "."},
                 "file_pattern": {"type": "string", "default": "*.tf"},
                 "case_sensitive": {"type": "boolean", "default": False},
             }, "required": ["pattern"]}),

        Tool(name="fs_git_diff",
             description="Get git diff of staged or unstaged changes",
             inputSchema={"type": "object", "properties": {
                 "staged": {"type": "boolean", "default": False},
                 "path": {"type": "string", "default": "."},
             }}),

        Tool(name="fs_run_command",
             description="Run a read-only shell command (terraform validate, checkov, etc.)",
             inputSchema={"type": "object", "properties": {
                 "command": {"type": "string", "description": "Shell command to execute"},
                 "cwd": {"type": "string", "default": "."},
                 "timeout": {"type": "integer", "default": 60},
             }, "required": ["command"]}),

        Tool(name="fs_get_tree",
             description="Get a compact directory tree representation",
             inputSchema={"type": "object", "properties": {
                 "path": {"type": "string", "default": "."},
                 "max_depth": {"type": "integer", "default": 4},
                 "include_hidden": {"type": "boolean", "default": False},
             }}),

        Tool(name="fs_delete_file",
             description="Delete a file (use with caution — checkpoint first)",
             inputSchema={"type": "object", "properties": {
                 "path": {"type": "string"},
             }, "required": ["path"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=str(result))]
    except PermissionError as e:
        return [TextContent(type="text", text=f"Permission denied: {e}")]
    except Exception as e:
        log.exception("mcp.fs.error", tool=name, error=str(e))
        return [TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict) -> Any:
    if name == "fs_read_file":
        path = _safe_path(args["path"])
        if not path.exists():
            return f"File not found: {args['path']}"
        if path.stat().st_size > MAX_FILE_SIZE:
            return f"File too large ({path.stat().st_size} bytes) — use fs_search_content"
        return path.read_text(encoding="utf-8", errors="replace")

    elif name == "fs_write_file":
        path = _safe_path(args["path"])
        if args.get("create_dirs", True):
            path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(args["content"], encoding="utf-8")
        return {"written": str(path.relative_to(REPO_ROOT)), "created": not existed, "bytes": len(args["content"])}

    elif name == "fs_list_dir":
        path = _safe_path(args.get("path", "."))
        if not path.exists():
            return f"Directory not found: {args.get('path', '.')}"
        pattern = args.get("pattern", "*")
        if args.get("recursive", False):
            items = list(path.rglob(pattern))
        else:
            items = list(path.glob(pattern))

        return [{"path": str(i.relative_to(REPO_ROOT)), "type": "dir" if i.is_dir() else "file",
                 "size": i.stat().st_size if i.is_file() else 0} for i in sorted(items)[:200]]

    elif name == "fs_search_content":
        base = _safe_path(args.get("path", "."))
        file_pattern = args.get("file_pattern", "*.tf")
        search_pattern = args["pattern"]
        flags = [] if args.get("case_sensitive") else ["-i"]

        result = subprocess.run(
            ["grep", "-r", *flags, "--include", file_pattern,
             "-n", "--color=never", search_pattern, str(base)],
            capture_output=True, text=True, timeout=15,
        )
        lines = result.stdout.splitlines()[:100]
        return {"matches": len(lines), "results": [
            {"file": l.split(":")[0].replace(str(REPO_ROOT) + "/", ""),
             "line": l.split(":")[1] if len(l.split(":")) > 1 else "?",
             "content": ":".join(l.split(":")[2:]).strip() if len(l.split(":")) > 2 else l}
            for l in lines
        ]}

    elif name == "fs_git_diff":
        cwd = _safe_path(args.get("path", "."))
        cmd = ["git", "diff", "--stat"]
        if args.get("staged"):
            cmd.append("--cached")
        result = subprocess.run(cmd + [str(cwd)], capture_output=True, text=True, timeout=15)
        return result.stdout[:10000]

    elif name == "fs_run_command":
        # Allowlist of safe read-only commands
        allowed_prefixes = [
            "terraform validate", "terraform fmt -check", "terraform show",
            "checkov", "tfsec", "actionlint", "opa check", "opa test", "pint lint",
            "git log", "git diff", "git status", "git show",
            "cat ", "ls ", "find ", "grep ", "head ", "tail ", "wc ",
            "yamllint", "jsonlint",
        ]
        cmd = args["command"]
        if not any(cmd.strip().startswith(p) for p in allowed_prefixes):
            return f"Command not in allowlist: '{cmd}'. Use explicit tools for write operations."

        cwd = _safe_path(args.get("cwd", "."))
        result = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True,
            timeout=args.get("timeout", 60),
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[:8000],
            "stderr": result.stderr[:2000],
        }

    elif name == "fs_get_tree":
        base = _safe_path(args.get("path", "."))
        max_depth = args.get("max_depth", 4)
        include_hidden = args.get("include_hidden", False)
        lines = []
        _build_tree(base, lines, prefix="", depth=0, max_depth=max_depth, include_hidden=include_hidden)
        return "\n".join(lines[:500])

    elif name == "fs_delete_file":
        path = _safe_path(args["path"])
        if not path.exists():
            return f"File not found: {args['path']}"
        if path.is_dir():
            return "Use explicit directory deletion — refusing to delete directory"
        path.unlink()
        return {"deleted": str(path.relative_to(REPO_ROOT))}

    return {"error": f"Unknown tool: {name}"}


def _build_tree(path: Path, lines: list, prefix: str, depth: int, max_depth: int, include_hidden: bool) -> None:
    if depth > max_depth:
        return
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
    except PermissionError:
        return

    entries = [e for e in entries if include_hidden or not e.name.startswith(".")]
    IGNORE = {".git", "__pycache__", ".terraform", "node_modules", ".venv", ".cloudforge"}
    entries = [e for e in entries if e.name not in IGNORE]

    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        icon = "📂 " if entry.is_dir() else ""
        lines.append(f"{prefix}{connector}{icon}{entry.name}")
        if entry.is_dir() and depth < max_depth:
            extension = "    " if i == len(entries) - 1 else "│   "
            _build_tree(entry, lines, prefix + extension, depth + 1, max_depth, include_hidden)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
