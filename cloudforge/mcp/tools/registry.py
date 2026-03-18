"""
CloudForge MCP Tool Registry
Maps @mention names to MCP server configs for the orchestrator.
Provides the tool schema used in Claude API tool_use calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str]
    env: dict[str, str] | None = None


# All available MCP servers
MCP_SERVERS: dict[str, MCPServerConfig] = {
    "github": MCPServerConfig(
        name="cloudforge-github",
        command="python",
        args=["-m", "cloudforge.mcp.servers.github_server"],
        env={"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
    ),
    "cloud": MCPServerConfig(
        name="cloudforge-cloud",
        command="python",
        args=["-m", "cloudforge.mcp.servers.cloud_server"],
    ),
    "filesystem": MCPServerConfig(
        name="cloudforge-filesystem",
        command="python",
        args=["-m", "cloudforge.mcp.servers.filesystem_server"],
        env={"CLOUDFORGE_REPO_ROOT": "${REPO_ROOT}"},
    ),
}


# Tool definitions for Claude API tool_use (used when NOT going through MCP)
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "write_file",
        "description": "Write generated infrastructure code to a file in the repository",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path from repo root"},
                "content": {"type": "string", "description": "File content to write"},
                "agent": {"type": "string", "enum": ["pipeline", "iac", "security", "observability"]},
            },
            "required": ["path", "content", "agent"],
        },
    },
    {
        "name": "read_file",
        "description": "Read an existing infrastructure file for context",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_terraform_validate",
        "description": "Validate Terraform configuration syntax",
        "input_schema": {
            "type": "object",
            "properties": {
                "module_path": {"type": "string", "description": "Path to TF module directory"},
            },
            "required": ["module_path"],
        },
    },
    {
        "name": "run_checkov_scan",
        "description": "Run Checkov security scan on a Terraform file",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "search_docs",
        "description": "Search the @Docs knowledge base for cloud provider docs, Terraform resources, security benchmarks",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "Filter by tag: aws, gcp, azure, oci, terraform, otel, cis, security"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_github_pr",
        "description": "Create a GitHub pull request with the generated files",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "branch": {"type": "string"},
                "files": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                }},
            },
            "required": ["repo", "title", "branch", "files"],
        },
    },
]


def get_tool_definitions(enabled: list[str] | None = None) -> list[dict]:
    """Return tool definitions, optionally filtered to a subset."""
    if enabled is None:
        return TOOL_DEFINITIONS
    return [t for t in TOOL_DEFINITIONS if t["name"] in enabled]
