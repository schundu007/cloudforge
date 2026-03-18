"""
CloudForge Web API
FastAPI backend with SSE streaming for real-time agent run logs.
Powers the web dashboard and serves the VS Code extension's remote API.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = structlog.get_logger()

app = FastAPI(
    title="CloudForge API",
    description="Cloud infrastructure coding agent — pipelines, IaC, security, observability",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory run registry (replace with Redis in production)
_runs: dict[str, dict] = {}


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------
class RunRequest(BaseModel):
    intent: str
    repo_root: str
    target_path: str | None = None
    github_token: str | None = None


class AcceptDiffRequest(BaseModel):
    run_id: str
    file_paths: list[str]  # which files to accept (empty = accept all)
    pr_title: str | None = None
    pr_body: str | None = None


class HookEventRequest(BaseModel):
    hook_type: str  # pre-commit, pr-opened, label, cost-threshold, drift, deploy, log-error
    payload: dict


# ------------------------------------------------------------------
# Core endpoints
# ------------------------------------------------------------------
@app.post("/runs")
async def create_run(req: RunRequest, background_tasks: BackgroundTasks) -> dict:
    """Start a new agent run. Returns run_id immediately; stream progress via SSE."""
    import uuid
    run_id = str(uuid.uuid4())
    _runs[run_id] = {"status": "pending", "events": [], "files": [], "diff": None}

    background_tasks.add_task(_execute_run, run_id, req)
    return {"run_id": run_id, "status": "started"}


@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    """SSE stream of agent events for a given run."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator() -> AsyncIterator[str]:
        last_idx = 0
        while True:
            run = _runs.get(run_id, {})
            events = run.get("events", [])
            for event in events[last_idx:]:
                yield f"data: {json.dumps(event)}\n\n"
                last_idx = len(events)
            if run.get("status") in ("complete", "error", "escalated"):
                yield f"data: {json.dumps({'event': 'done', 'status': run['status']})}\n\n"
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return _runs[run_id]


@app.get("/runs/{run_id}/diff")
async def get_diff(run_id: str) -> dict:
    """Get the generated diff for review (Firebender-style accept/reject UX)."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.get("status") != "complete":
        raise HTTPException(status_code=409, detail=f"Run status: {run.get('status')}")
    return {"diff": run.get("diff"), "files": run.get("files", [])}


@app.post("/runs/{run_id}/accept")
async def accept_diff(run_id: str, req: AcceptDiffRequest) -> dict:
    """Accept diff and push to GitHub as a PR."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    files = run.get("files", [])
    if req.file_paths:
        files = [f for f in files if f["path"] in req.file_paths]

    # In production: commit files to feature branch, open PR via GitHub API
    pr_url = await _push_to_github(files, req.pr_title or "CloudForge: automated infra update")
    _runs[run_id]["pr_url"] = pr_url
    _runs[run_id]["status"] = "pr_opened"
    return {"pr_url": pr_url, "files_accepted": [f["path"] for f in files]}


@app.post("/runs/{run_id}/reject")
async def reject_diff(run_id: str, feedback: dict) -> dict:
    """Reject diff with feedback — triggers a new fix iteration."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    _runs[run_id]["status"] = "rejected"
    _runs[run_id]["rejection_feedback"] = feedback.get("message", "")
    return {"status": "rejected", "message": "Diff rejected. Start a new run with updated intent."}


@app.post("/hooks")
async def handle_hook(req: HookEventRequest, background_tasks: BackgroundTasks) -> dict:
    """Receive webhook events and trigger appropriate agent actions."""
    from cloudforge.core.hooks.dispatcher import HookDispatcher
    dispatcher = HookDispatcher()
    run_id = await dispatcher.dispatch(req.hook_type, req.payload)
    return {"run_id": run_id, "hook_type": req.hook_type}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


# ------------------------------------------------------------------
# Background task
# ------------------------------------------------------------------
async def _execute_run(run_id: str, req: RunRequest) -> None:
    """Execute the full orchestrator loop, append events to run registry."""
    try:
        from cloudforge.core.agents.orchestrator import Orchestrator

        orchestrator = Orchestrator(
            repo_root=Path(req.repo_root),
            github_token=req.github_token or os.getenv("GITHUB_TOKEN", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        )

        _runs[run_id]["status"] = "running"

        async for event in orchestrator.run(req.intent, req.target_path):
            _runs[run_id]["events"].append(event)

            if event.get("event") == "diff_ready":
                _runs[run_id]["files"] = event.get("files", [])
                _runs[run_id]["diff"] = event.get("diff")
                _runs[run_id]["status"] = "complete"

            elif event.get("event") == "escalate":
                _runs[run_id]["status"] = "escalated"

    except Exception as e:
        log.exception("run.failed", run_id=run_id, error=str(e))
        _runs[run_id]["status"] = "error"
        _runs[run_id]["error"] = str(e)


async def _push_to_github(files: list[dict], title: str) -> str:
    """Stub: push files to feature branch and open PR. Wire up PyGithub in production."""
    # TODO: implement with PyGithub
    branch = f"cloudforge/{title.lower().replace(' ', '-')[:40]}"
    return f"https://github.com/your-org/your-repo/pull/999  # branch: {branch}"
