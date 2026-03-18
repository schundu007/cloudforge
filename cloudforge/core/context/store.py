"""
CloudForge Context Store
Shared infra state read by all sub-agents on every call.
Mirrors Firebender's codebase-aware context (repo tree, Logcat, project files).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


@dataclass
class RepoContext:
    root: Path
    tree: dict[str, Any] = field(default_factory=dict)       # file tree
    tf_state: dict[str, Any] = field(default_factory=dict)   # terraform show -json
    tf_modules: list[str] = field(default_factory=list)       # discovered module paths
    gha_workflows: list[str] = field(default_factory=list)    # .github/workflows/*.yml
    active_prs: list[dict] = field(default_factory=list)      # open PRs from GitHub MCP
    checkov_baseline: dict[str, Any] = field(default_factory=dict)
    cloud_resources: dict[str, Any] = field(default_factory=dict)  # discovered live infra


class ContextStore:
    """
    Single source of truth for all CloudForge agents.
    Persisted to .cloudforge/context.json in the repo root.
    Refreshed before every agent invocation.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self._state_path = repo_root / ".cloudforge" / "context.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self.ctx = RepoContext(root=repo_root)

    # ------------------------------------------------------------------
    # Refresh pipeline
    # ------------------------------------------------------------------
    def refresh(self) -> RepoContext:
        log.info("context.refresh", root=str(self.repo_root))
        self._scan_repo_tree()
        self._load_tf_state()
        self._discover_modules()
        self._discover_workflows()
        self._save()
        return self.ctx

    def _scan_repo_tree(self, max_depth: int = 5) -> None:
        """Walk repo, build lightweight tree (paths only, no content)."""
        tree: dict[str, Any] = {}
        for p in self.repo_root.rglob("*"):
            if any(part.startswith(".") for part in p.parts):
                continue
            if any(exc in p.parts for exc in ["node_modules", "__pycache__", ".terraform"]):
                continue
            rel = p.relative_to(self.repo_root)
            depth = len(rel.parts)
            if depth > max_depth:
                continue
            tree[str(rel)] = {"type": "dir" if p.is_dir() else "file", "size": p.stat().st_size if p.is_file() else 0}
        self.ctx.tree = tree

    def _load_tf_state(self) -> None:
        """Run terraform show -json to capture current state."""
        tf_dirs = [p.parent for p in self.repo_root.rglob("*.tfstate")]
        if not tf_dirs:
            return
        try:
            result = subprocess.run(
                ["terraform", "show", "-json"],
                cwd=tf_dirs[0],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                self.ctx.tf_state = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            log.warning("tf_state.load_failed", error=str(e))

    def _discover_modules(self) -> None:
        self.ctx.tf_modules = [
            str(p.parent.relative_to(self.repo_root))
            for p in self.repo_root.rglob("main.tf")
        ]

    def _discover_workflows(self) -> None:
        wf_dir = self.repo_root / ".github" / "workflows"
        if wf_dir.exists():
            self.ctx.gha_workflows = [str(f.relative_to(self.repo_root)) for f in wf_dir.glob("*.yml")]

    def _save(self) -> None:
        data = {
            "tree": self.ctx.tree,
            "tf_modules": self.ctx.tf_modules,
            "gha_workflows": self.ctx.gha_workflows,
            "tf_state_resources": list(self.ctx.tf_state.get("values", {}).get("root_module", {}).get("resources", [])),
            "cloud_resources": self.ctx.cloud_resources,
        }
        self._state_path.write_text(json.dumps(data, indent=2))

    def as_prompt_context(self) -> str:
        """Serialize context for injection into agent system prompt."""
        return json.dumps({
            "repo_root": str(self.repo_root),
            "tf_modules": self.ctx.tf_modules,
            "gha_workflows": self.ctx.gha_workflows,
            "file_count": len(self.ctx.tree),
            "tf_resources": len(self.ctx.tf_state.get("values", {}).get("root_module", {}).get("resources", [])),
            "cloud_resources_discovered": bool(self.ctx.cloud_resources),
        }, indent=2)
