"""
CloudForge GitHub App
Handles GitHub webhook events: PR opened/labeled, issue commands,
push to main. Posts diff reviews as PR comments. Auto-merges on pass.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks

log = structlog.get_logger()
app = FastAPI(title="CloudForge GitHub App")

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
CLOUDFORGE_API = os.getenv("CLOUDFORGE_API_URL", "http://localhost:8000")


@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Receive GitHub webhook events and dispatch to CloudForge."""
    body = await request.body()
    _verify_signature(body, request.headers.get("X-Hub-Signature-256", ""))

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)

    log.info("github.webhook", event=event, action=payload.get("action"))

    if event == "pull_request":
        background_tasks.add_task(_handle_pr, payload)
    elif event == "pull_request_review_comment":
        background_tasks.add_task(_handle_pr_comment, payload)
    elif event == "push" and payload.get("ref") == "refs/heads/main":
        background_tasks.add_task(_handle_push_to_main, payload)
    elif event == "issues" and payload.get("action") == "opened":
        background_tasks.add_task(_handle_issue, payload)

    return {"status": "accepted"}


async def _handle_pr(payload: dict) -> None:
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})

    # Trigger on PR open or sync
    if action not in ("opened", "synchronize"):
        return

    # Check for CloudForge command in PR body
    body = pr.get("body", "") or ""
    labels = [l["name"] for l in pr.get("labels", [])]

    files_changed = await _get_pr_files(repo["full_name"], pr["number"], pr["head"]["sha"])

    # Always: run terraform plan comment
    if any(f.endswith(".tf") for f in files_changed):
        await _dispatch_hook("pr-opened", {
            "pull_request": pr,
            "repo": repo["full_name"],
            "files_changed": files_changed,
        })

    # Label-driven: add observability
    if "cf-add-obs" in labels:
        await _dispatch_hook("label:cf-add-obs", {"pull_request": pr, "repo": repo["full_name"]})

    # Label-driven: add security policies
    if "cf-add-security" in labels:
        await _dispatch_hook("label:cf-add-security", {"pull_request": pr, "repo": repo["full_name"]})

    # Slash command in PR body
    if "/cloudforge generate" in body:
        intent = body.split("/cloudforge generate", 1)[1].strip().splitlines()[0]
        await _post_pr_comment(
            repo["full_name"], pr["number"],
            f"🤖 CloudForge is generating: `{intent}`..."
        )
        run_id = await _start_run(intent, repo["clone_url"])
        await _post_pr_comment(
            repo["full_name"], pr["number"],
            f"🏃 Run started: `{run_id}` — streaming at {CLOUDFORGE_API}/runs/{run_id}/stream"
        )


async def _handle_pr_comment(payload: dict) -> None:
    """React to review comments suggesting CloudForge changes."""
    comment = payload.get("comment", {})
    body = comment.get("body", "")
    if "/cloudforge fix" not in body:
        return
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    intent = body.split("/cloudforge fix", 1)[1].strip()
    run_id = await _start_run(intent, repo.get("clone_url", ""))
    await _post_pr_comment(repo["full_name"], pr["number"],
                           f"🔧 CloudForge fix started: `{run_id}`")


async def _handle_push_to_main(payload: dict) -> None:
    """On push to main: check for drift, trigger deploy security gate."""
    repo = payload.get("repository", {})
    commits = payload.get("commits", [])
    tf_changed = any(
        f.endswith(".tf")
        for c in commits
        for f in c.get("modified", []) + c.get("added", [])
    )
    if tf_changed:
        await _dispatch_hook("deploy:prod", {"repo": repo["full_name"], "ref": payload.get("after")})


async def _handle_issue(payload: dict) -> None:
    """React to issue body containing /cloudforge commands."""
    issue = payload.get("issue", {})
    body = issue.get("body", "") or ""
    if "/cloudforge" not in body:
        return
    # Parse intent from issue body
    for line in body.splitlines():
        if line.strip().startswith("/cloudforge"):
            intent = line.replace("/cloudforge", "").strip()
            repo = payload.get("repository", {})
            await _start_run(intent, repo.get("clone_url", ""))
            break


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
async def _start_run(intent: str, repo_clone_url: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{CLOUDFORGE_API}/runs", json={
            "intent": intent,
            "repo_root": "/tmp/cloudforge-workspace",  # cloned repo path
        }, timeout=10)
        resp.raise_for_status()
        return resp.json()["run_id"]


async def _dispatch_hook(hook_type: str, payload: dict) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(f"{CLOUDFORGE_API}/hooks", json={
            "hook_type": hook_type,
            "payload": payload,
        }, timeout=10)


async def _get_pr_files(repo: str, pr_number: int, sha: str) -> list[str]:
    token = os.getenv("GITHUB_TOKEN", "")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=15,
        )
        if resp.status_code == 200:
            return [f["filename"] for f in resp.json()]
    return []


async def _post_pr_comment(repo: str, pr_number: int, body: str) -> None:
    token = os.getenv("GITHUB_TOKEN", "")
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            json={"body": body},
            timeout=10,
        )


def _verify_signature(body: bytes, sig_header: str) -> None:
    if not WEBHOOK_SECRET:
        return
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
