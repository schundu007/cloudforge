"""
CloudForge Background Agent
Runs long-running agent tasks in isolated git worktrees.
Mirrors Firebender's background agent that branches off the latest local branch.

Each task gets its own worktree → no branch conflicts → parallel tasks safe.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import structlog

log = structlog.get_logger()


@dataclass
class WorktreeTask:
    task_id: str
    intent: str
    worktree_path: Path
    branch: str
    status: str = "pending"     # pending | running | complete | failed
    events: list[dict] = field(default_factory=list)
    result_files: list[dict] = field(default_factory=list)
    error: str | None = None


class BackgroundAgent:
    """
    Manages a pool of git worktrees for isolated agent tasks.

    Usage:
        agent = BackgroundAgent(repo_root=Path("."))
        task = await agent.start("refactor all hardcoded AMI IDs to data sources")
        async for event in agent.stream(task.task_id):
            print(event)
    """

    def __init__(self, repo_root: Path, worktree_base: Path | None = None):
        self.repo_root = repo_root.resolve()
        self.worktree_base = worktree_base or (repo_root / ".cloudforge" / "worktrees")
        self.worktree_base.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, WorktreeTask] = {}
        self._running: dict[str, asyncio.Task] = {}

    async def start(self, intent: str) -> WorktreeTask:
        """Create a worktree, start the agent task in it, return immediately."""
        task_id = str(uuid.uuid4())[:8]
        branch = f"cloudforge/bg-{task_id}"
        worktree_path = self.worktree_base / task_id

        log.info("background_agent.start", task_id=task_id, branch=branch, intent=intent[:80])

        # Create git worktree branching from current HEAD
        try:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(worktree_path)],
                cwd=self.repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to create worktree: {e.stderr}") from e

        task = WorktreeTask(
            task_id=task_id,
            intent=intent,
            worktree_path=worktree_path,
            branch=branch,
        )
        self._tasks[task_id] = task

        # Fire and forget — runs in background
        bg = asyncio.create_task(self._run_task(task))
        self._running[task_id] = bg

        return task

    async def stream(self, task_id: str) -> AsyncIterator[dict]:
        """Yield events for a background task until completion."""
        task = self._tasks.get(task_id)
        if not task:
            yield {"event": "error", "message": f"Task {task_id} not found"}
            return

        last = 0
        while True:
            events = task.events
            for ev in events[last:]:
                yield ev
            last = len(events)

            if task.status in ("complete", "failed"):
                yield {"event": "done", "status": task.status,
                       "branch": task.branch, "files": task.result_files}
                break
            await asyncio.sleep(0.3)

    def get_task(self, task_id: str) -> WorktreeTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[WorktreeTask]:
        return list(self._tasks.values())

    async def cleanup(self, task_id: str) -> None:
        """Remove worktree and prune git references."""
        task = self._tasks.get(task_id)
        if not task:
            return
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(task.worktree_path)],
                cwd=self.repo_root, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=self.repo_root, capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            log.warning("background_agent.cleanup_failed", task_id=task_id, error=str(e))
        finally:
            if task.worktree_path.exists():
                shutil.rmtree(task.worktree_path, ignore_errors=True)
            del self._tasks[task_id]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _run_task(self, task: WorktreeTask) -> None:
        """Execute the full orchestrator loop inside the worktree."""
        import os
        from cloudforge.core.agents.orchestrator import Orchestrator

        task.status = "running"
        task.events.append({"event": "worktree_ready", "path": str(task.worktree_path), "branch": task.branch})

        try:
            orch = Orchestrator(
                repo_root=task.worktree_path,
                github_token=os.getenv("GITHUB_TOKEN", ""),
                anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            )

            async for event in orch.run(task.intent):
                task.events.append(event)

                if event.get("event") == "diff_ready":
                    task.result_files = event.get("files", [])
                    # Auto-commit generated files to the worktree branch
                    await self._commit_to_worktree(task)

                elif event.get("event") == "escalate":
                    task.status = "failed"
                    task.error = event.get("message")
                    return

            task.status = "complete"
            task.events.append({
                "event": "complete",
                "branch": task.branch,
                "files_changed": [f["path"] for f in task.result_files],
                "message": f"Ready to open PR: git push origin {task.branch}",
            })

        except Exception as e:
            log.exception("background_agent.task_failed", task_id=task.task_id, error=str(e))
            task.status = "failed"
            task.error = str(e)
            task.events.append({"event": "error", "message": str(e)})

    async def _commit_to_worktree(self, task: WorktreeTask) -> None:
        """Write generated files and commit them in the worktree."""
        for f in task.result_files:
            file_path = task.worktree_path / f["path"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(f["content"])

        try:
            subprocess.run(["git", "add", "."], cwd=task.worktree_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", f"CloudForge: {task.intent[:72]}"],
                cwd=task.worktree_path, check=True, capture_output=True,
                env={**__import__("os").environ,
                     "GIT_AUTHOR_NAME": "CloudForge",
                     "GIT_AUTHOR_EMAIL": "agent@cloudforge.dev",
                     "GIT_COMMITTER_NAME": "CloudForge",
                     "GIT_COMMITTER_EMAIL": "agent@cloudforge.dev"},
            )
            task.events.append({"event": "committed", "branch": task.branch})
        except subprocess.CalledProcessError as e:
            log.warning("background_agent.commit_failed", error=e.stderr)
