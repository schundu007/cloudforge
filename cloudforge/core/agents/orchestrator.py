"""
CloudForge Orchestrator Agent
Routes tasks to specialized sub-agents, manages shared context,
drives the auto-fix loop. Powered by Claude + MCP tool use.

Mirrors Firebender's multi-agent system architecture.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator

import anthropic
import structlog

from cloudforge.core.context.store import ContextStore
from cloudforge.core.agents.pipeline_writer import PipelineWriter
from cloudforge.core.agents.iac_writer import IaCWriter
from cloudforge.core.agents.security_writer import SecurityWriter
from cloudforge.core.agents.observability_writer import ObservabilityWriter
from cloudforge.core.emulator.runner import EmulatorRunner
from cloudforge.core.parsers.error_parser import ErrorParser

log = structlog.get_logger()

MODEL = "claude-sonnet-4-6"
MAX_FIX_RETRIES = 5


class TaskType(str, Enum):
    PIPELINE = "pipeline"
    IAC = "iac"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    MULTI = "multi"


@dataclass
class AgentResult:
    task_type: TaskType
    files_changed: list[dict]   # [{path, content, diff}]
    test_passed: bool
    fix_iterations: int
    checkov_passed: bool
    pr_url: str | None = None
    error: str | None = None


class Orchestrator:
    """
    Master agent that:
    1. Classifies intent -> TaskType
    2. Routes to specialist sub-agent
    3. Runs emulator test cycle
    4. Auto-fixes failures (max MAX_FIX_RETRIES)
    5. Creates GitHub PR with diff for review
    """

    SYSTEM_PROMPT = """You are CloudForge, a cloud infrastructure coding agent.
You write GitHub Actions pipelines, Terraform IaC, security policies (IAM/OPA/Checkov),
and observability configs (OTel/Prometheus/Grafana) for AWS, GCP, Azure, and OCI.

Current infrastructure context:
{context}

Rules:
- Always follow security-first: every generated resource is scanned before presenting the diff
- Use naming convention: [prefix]-[project]-[suffix][env]-[random]-[resource]
- Prefer OIDC over static credentials in all pipelines
- Terraform resources must comply with checkov CKV_AWS_*, CKV_GCP_*, CKV_AZURE_*
- All IAM policies must be least-privilege
- Never expose secrets — scrub before writing to files

When you need to generate files, call the appropriate tool. Always explain what you changed and why.
"""

    def __init__(self, repo_root: Path, github_token: str, anthropic_api_key: str):
        self.repo_root = repo_root
        self.github_token = github_token
        self.client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)
        self.context = ContextStore(repo_root)
        self.emulator = EmulatorRunner(repo_root)
        self.error_parser = ErrorParser()

        # Sub-agents
        self.agents = {
            TaskType.PIPELINE: PipelineWriter(repo_root),
            TaskType.IAC: IaCWriter(repo_root),
            TaskType.SECURITY: SecurityWriter(repo_root),
            TaskType.OBSERVABILITY: ObservabilityWriter(repo_root),
        }

    async def run(self, intent: str, target_path: str | None = None) -> AsyncIterator[dict]:
        """
        Main entry point. Streams progress events back to the UI/CLI.
        Mirrors Firebender's agent mode: write files -> run emulator -> iterate -> git diff.
        """
        ctx = self.context.refresh()
        yield {"event": "context_loaded", "tf_modules": ctx.tf_modules, "workflows": ctx.gha_workflows}

        # Step 1: Classify intent
        task_type = await self._classify(intent)
        yield {"event": "classified", "task_type": task_type.value}

        # Step 2: Route to sub-agent(s)
        if task_type == TaskType.MULTI:
            sub_tasks = await self._decompose(intent)
        else:
            sub_tasks = [(task_type, intent)]

        all_files: list[dict] = []
        for t_type, t_intent in sub_tasks:
            yield {"event": "agent_start", "agent": t_type.value, "intent": t_intent}
            agent = self.agents[t_type]
            files = await agent.generate(t_intent, ctx, self.context.as_prompt_context())
            all_files.extend(files)
            yield {"event": "agent_done", "agent": t_type.value, "files": [f["path"] for f in files]}

        # Step 3: Emulator test cycle with auto-fix loop
        fix_count = 0
        while fix_count <= MAX_FIX_RETRIES:
            yield {"event": "emulator_start", "iteration": fix_count}
            result = await self.emulator.run(all_files)

            if result.passed:
                yield {"event": "emulator_pass", "summary": result.summary}
                break

            yield {"event": "emulator_fail", "errors": result.errors, "iteration": fix_count}

            if fix_count == MAX_FIX_RETRIES:
                yield {"event": "escalate", "message": "Max retries reached — human review required", "errors": result.errors}
                break

            # Parse errors and generate fix
            patch_request = self.error_parser.parse(result.errors)
            yield {"event": "fix_start", "patches": len(patch_request.patches)}

            fixed = await self._apply_fix(patch_request, all_files)
            all_files = fixed
            fix_count += 1

        # Step 4: Present diff for accept/reject
        diff = self._build_diff(all_files)
        yield {"event": "diff_ready", "diff": diff, "files": all_files, "fix_iterations": fix_count}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _classify(self, intent: str) -> TaskType:
        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=50,
            system="Classify the user's infrastructure intent. Reply with exactly one word: pipeline, iac, security, observability, or multi.",
            messages=[{"role": "user", "content": intent}],
        )
        word = resp.content[0].text.strip().lower()
        try:
            return TaskType(word)
        except ValueError:
            return TaskType.MULTI

    async def _decompose(self, intent: str) -> list[tuple[TaskType, str]]:
        """Break multi-intent into typed sub-tasks."""
        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=500,
            system='Return a JSON array of {type, intent} objects. Types: pipeline, iac, security, observability.',
            messages=[{"role": "user", "content": intent}],
        )
        try:
            tasks = json.loads(resp.content[0].text)
            return [(TaskType(t["type"]), t["intent"]) for t in tasks]
        except (json.JSONDecodeError, KeyError, ValueError):
            return [(TaskType.MULTI, intent)]

    async def _apply_fix(self, patch_request: Any, files: list[dict]) -> list[dict]:
        """Call the appropriate sub-agent to fix a specific error."""
        agent = self.agents.get(patch_request.agent_type, self.agents[TaskType.IAC])
        return await agent.fix(patch_request, files, self.context.as_prompt_context())

    def _build_diff(self, files: list[dict]) -> str:
        """Assemble unified diff across all changed files."""
        lines = []
        for f in files:
            lines.append(f"diff --cloudforge a/{f['path']} b/{f['path']}")
            lines.append(f.get("diff", f"--- /dev/null\n+++ b/{f['path']}\n{f['content']}"))
        return "\n".join(lines)
