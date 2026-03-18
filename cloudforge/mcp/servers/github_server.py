"""
CloudForge MCP Server — GitHub
Exposes GitHub operations as MCP tools for the orchestrator agent.
Tools: read_file, list_files, create_branch, commit_files, open_pr,
       get_pr_files, post_pr_comment, list_workflows, get_run_logs
"""
from __future__ import annotations

import base64
import os
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from github import Github, GithubException

log = structlog.get_logger()

server = Server("cloudforge-github")
_gh: Github | None = None


def _client() -> Github:
    global _gh
    if _gh is None:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("GITHUB_TOKEN env var required")
        _gh = Github(token)
    return _gh


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="github_read_file",
             description="Read a file from a GitHub repo at a specific ref",
             inputSchema={"type": "object", "properties": {
                 "repo": {"type": "string", "description": "owner/repo"},
                 "path": {"type": "string"},
                 "ref": {"type": "string", "default": "main"},
             }, "required": ["repo", "path"]}),

        Tool(name="github_list_files",
             description="List files in a directory of a GitHub repo",
             inputSchema={"type": "object", "properties": {
                 "repo": {"type": "string"},
                 "path": {"type": "string", "default": ""},
                 "ref": {"type": "string", "default": "main"},
             }, "required": ["repo"]}),

        Tool(name="github_create_branch",
             description="Create a new branch from base",
             inputSchema={"type": "object", "properties": {
                 "repo": {"type": "string"},
                 "branch": {"type": "string"},
                 "base": {"type": "string", "default": "main"},
             }, "required": ["repo", "branch"]}),

        Tool(name="github_commit_files",
             description="Commit one or more files to a branch",
             inputSchema={"type": "object", "properties": {
                 "repo": {"type": "string"},
                 "branch": {"type": "string"},
                 "files": {"type": "array", "items": {
                     "type": "object",
                     "properties": {
                         "path": {"type": "string"},
                         "content": {"type": "string"},
                     }
                 }},
                 "message": {"type": "string"},
             }, "required": ["repo", "branch", "files", "message"]}),

        Tool(name="github_open_pr",
             description="Open a pull request",
             inputSchema={"type": "object", "properties": {
                 "repo": {"type": "string"},
                 "title": {"type": "string"},
                 "body": {"type": "string"},
                 "head": {"type": "string"},
                 "base": {"type": "string", "default": "main"},
             }, "required": ["repo", "title", "head"]}),

        Tool(name="github_post_comment",
             description="Post a comment on a PR or issue",
             inputSchema={"type": "object", "properties": {
                 "repo": {"type": "string"},
                 "number": {"type": "integer"},
                 "body": {"type": "string"},
             }, "required": ["repo", "number", "body"]}),

        Tool(name="github_get_workflow_run_logs",
             description="Get logs from a GitHub Actions workflow run",
             inputSchema={"type": "object", "properties": {
                 "repo": {"type": "string"},
                 "run_id": {"type": "integer"},
             }, "required": ["repo", "run_id"]}),

        Tool(name="github_list_open_prs",
             description="List open pull requests for a repo",
             inputSchema={"type": "object", "properties": {
                 "repo": {"type": "string"},
             }, "required": ["repo"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=str(result))]
    except GithubException as e:
        return [TextContent(type="text", text=f"GitHub error {e.status}: {e.data}")]
    except Exception as e:
        log.exception("mcp.github.error", tool=name, error=str(e))
        return [TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict) -> Any:
    gh = _client()
    repo = gh.get_repo(args["repo"]) if "repo" in args else None

    if name == "github_read_file":
        content = repo.get_contents(args["path"], ref=args.get("ref", "main"))
        return base64.b64decode(content.content).decode("utf-8")

    elif name == "github_list_files":
        contents = repo.get_contents(args.get("path", ""), ref=args.get("ref", "main"))
        if isinstance(contents, list):
            return [{"path": c.path, "type": c.type, "size": c.size} for c in contents]
        return [{"path": contents.path, "type": contents.type}]

    elif name == "github_create_branch":
        base_ref = repo.get_branch(args.get("base", "main"))
        repo.create_git_ref(
            ref=f"refs/heads/{args['branch']}",
            sha=base_ref.commit.sha,
        )
        return {"branch": args["branch"], "sha": base_ref.commit.sha}

    elif name == "github_commit_files":
        results = []
        for f in args["files"]:
            try:
                existing = repo.get_contents(f["path"], ref=args["branch"])
                resp = repo.update_file(
                    path=f["path"], message=args["message"],
                    content=f["content"], sha=existing.sha, branch=args["branch"],
                )
            except GithubException:
                resp = repo.create_file(
                    path=f["path"], message=args["message"],
                    content=f["content"], branch=args["branch"],
                )
            results.append({"path": f["path"], "sha": resp["commit"].sha})
        return results

    elif name == "github_open_pr":
        pr = repo.create_pull(
            title=args["title"],
            body=args.get("body", "CloudForge automated PR"),
            head=args["head"],
            base=args.get("base", "main"),
        )
        return {"number": pr.number, "url": pr.html_url, "title": pr.title}

    elif name == "github_post_comment":
        issue = repo.get_issue(args["number"])
        comment = issue.create_comment(args["body"])
        return {"id": comment.id, "url": comment.html_url}

    elif name == "github_get_workflow_run_logs":
        run = repo.get_workflow_run(args["run_id"])
        return {"status": run.status, "conclusion": run.conclusion,
                "url": run.html_url, "created_at": str(run.created_at)}

    elif name == "github_list_open_prs":
        prs = repo.get_pulls(state="open")
        return [{"number": pr.number, "title": pr.title, "head": pr.head.ref,
                 "url": pr.html_url} for pr in prs[:20]]

    return {"error": f"Unknown tool: {name}"}


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
