"""
CloudForge Web API
FastAPI backend with SSE streaming for real-time agent run logs.
Powers the web dashboard and serves the VS Code extension's remote API.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv

# Load .env so ANTHROPIC_API_KEY / GITHUB_TOKEN are available when the
# process is started without them exported (documented setup in README).
load_dotenv()

import structlog
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from cloudforge.core.git.workspace import RepoError, RepoWorkspace
from cloudforge.core.github.pr import PullRequestError, open_pull_request

log = structlog.get_logger()

app = FastAPI(
    title="CloudForge API",
    description="Cloud infrastructure coding agent — pipelines, IaC, security, observability",
    version="0.1.0",
)

# CORS: locked to the dashboard origins in production. Set
# CLOUDFORGE_ALLOWED_ORIGINS as a comma-separated list; "*" re-opens it.
_origins_env = os.getenv("CLOUDFORGE_ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS = (
    [o.strip() for o in _origins_env.split(",") if o.strip()]
    if _origins_env
    else ["http://localhost:5173", "http://127.0.0.1:5173"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared-secret gate. When CLOUDFORGE_API_TOKEN is set, every endpoint except
# /health requires `Authorization: Bearer <token>`. Unset = open (local dev).
API_TOKEN = os.getenv("CLOUDFORGE_API_TOKEN", "").strip()
_PUBLIC_PATHS = {"/health"}


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    if not API_TOKEN or request.method == "OPTIONS" or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    presented = credential.strip() if scheme.lower() == "bearer" else ""
    # Constant-time compare so the token can't be recovered by timing.
    if not presented or not secrets.compare_digest(presented, API_TOKEN):
        log.warning("api.unauthorized", path=request.url.path, client=request.client.host if request.client else "?")
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    return await call_next(request)

# In-memory run registry (replace with Redis in production)
_runs: dict[str, dict] = {}


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------
class RunRequest(BaseModel):
    intent: str
    # Either clone a repo for this run (deployment) or point at a local path
    # (development). repo_url wins when both are given.
    repo_url: str | None = None
    branch: str | None = None
    repo_root: str | None = None
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
    if not req.repo_url and not req.repo_root:
        raise HTTPException(status_code=422, detail="Provide repo_url or repo_root")

    run_id = str(uuid.uuid4())
    _runs[run_id] = {
        "status": "pending",
        "events": [],
        "files": [],
        "diff": None,
        "repo_url": req.repo_url,
        "branch": req.branch,
        "github_token": req.github_token,
    }

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
                # Carry the failure reason to the client; a bare "done" left the
                # dashboard showing a finished run with no explanation.
                terminal = {"event": "done", "status": run["status"]}
                if run.get("error"):
                    terminal["message"] = str(run["error"])
                yield f"data: {json.dumps(terminal)}\n\n"
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

    repo_url = run.get("repo_url")
    if not repo_url:
        raise HTTPException(
            status_code=409,
            detail="This run has no target repository. Start the run with repo_url to open a PR.",
        )

    title = req.pr_title or "CloudForge: automated infra update"
    token = run.get("github_token") or os.getenv("GITHUB_TOKEN", "")

    try:
        result = await asyncio.to_thread(
            open_pull_request,
            repo_url=repo_url,
            files=files,
            title=title,
            token=token,
            body=req.pr_body,
            base=run.get("branch"),
        )
    except (PullRequestError, RepoError) as exc:
        log.warning("accept.pr_failed", run_id=run_id, error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _runs[run_id]["pr_url"] = result.url
    _runs[run_id]["status"] = "pr_opened"
    return {
        "pr_url": result.url,
        "branch": result.branch,
        "base": result.base,
        "files_accepted": result.files,
    }


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
    workspace: RepoWorkspace | None = None
    try:
        from cloudforge.core.agents.orchestrator import Orchestrator

        token = req.github_token or os.getenv("GITHUB_TOKEN", "")

        if req.repo_url:
            # Clone the target repo for this run; the container's own filesystem
            # is not a meaningful place to generate infrastructure.
            workspace = await asyncio.to_thread(
                lambda: RepoWorkspace(req.repo_url, branch=req.branch, token=token).open()
            )
            repo_root = workspace.path
            _runs[run_id]["events"].append(
                {"event": "repo_cloned", "repo": workspace.ref.full_name,
                 "branch": req.branch or "(default)"}
            )
        else:
            repo_root = Path(req.repo_root or ".")

        orchestrator = Orchestrator(
            repo_root=repo_root,
            github_token=token,
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

    except RepoError as e:
        log.warning("run.clone_failed", run_id=run_id, error=str(e))
        _runs[run_id]["status"] = "error"
        _runs[run_id]["error"] = str(e)
    except Exception as e:
        log.exception("run.failed", run_id=run_id, error=str(e))
        _runs[run_id]["status"] = "error"
        _runs[run_id]["error"] = str(e)
    finally:
        if workspace is not None:
            workspace.close()



