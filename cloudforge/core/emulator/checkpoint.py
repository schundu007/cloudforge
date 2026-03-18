"""
CloudForge Checkpoint System
Snapshots tf state + git stash before every apply.
One-click revert to any checkpoint.
Mirrors Firebender's checkpointing: continue/revert after agent changes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog

log = structlog.get_logger()

CHECKPOINT_DIR = ".cloudforge/checkpoints"


@dataclass
class Checkpoint:
    id: str
    created_at: str
    intent: str
    tf_state_path: str | None      # copied tfstate file path
    git_stash_ref: str | None      # git stash ref (stash@{n})
    files_snapshot: list[str]      # list of files that were modified
    status: str = "active"         # active | reverted | applied


class CheckpointManager:
    """
    Creates and manages checkpoints before CloudForge applies changes.

    Workflow:
        ckpt = manager.create("add NAT gateway")
        # ... agent runs, files written ...
        if something_went_wrong:
            manager.revert(ckpt.id)
        else:
            manager.mark_applied(ckpt.id)
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.ckpt_dir = repo_root / CHECKPOINT_DIR
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.ckpt_dir / "index.json"

    def create(self, intent: str, files_to_snapshot: list[str] | None = None) -> Checkpoint:
        """Snapshot current state before agent applies changes."""
        import uuid
        ckpt_id = str(uuid.uuid4())[:8]
        timestamp = datetime.utcnow().isoformat()

        log.info("checkpoint.create", id=ckpt_id, intent=intent[:60])

        # 1. Snapshot terraform state
        tf_state_path = self._snapshot_tf_state(ckpt_id)

        # 2. Git stash with a named reference
        stash_ref = self._git_stash(ckpt_id, intent)

        ckpt = Checkpoint(
            id=ckpt_id,
            created_at=timestamp,
            intent=intent,
            tf_state_path=str(tf_state_path) if tf_state_path else None,
            git_stash_ref=stash_ref,
            files_snapshot=files_to_snapshot or [],
        )
        self._save_checkpoint(ckpt)
        return ckpt

    def revert(self, checkpoint_id: str) -> dict:
        """Revert to a specific checkpoint."""
        ckpt = self._load_checkpoint(checkpoint_id)
        if not ckpt:
            return {"success": False, "error": f"Checkpoint {checkpoint_id} not found"}

        log.info("checkpoint.revert", id=checkpoint_id, intent=ckpt.intent[:60])
        errors = []

        # 1. Restore tf state
        if ckpt.tf_state_path:
            restored = self._restore_tf_state(ckpt.tf_state_path)
            if not restored:
                errors.append("tf state restore failed")

        # 2. Pop git stash
        if ckpt.git_stash_ref:
            popped = self._git_stash_pop(ckpt.git_stash_ref)
            if not popped:
                errors.append("git stash pop failed")

        ckpt.status = "reverted"
        self._save_checkpoint(ckpt)

        return {
            "success": len(errors) == 0,
            "errors": errors,
            "checkpoint_id": checkpoint_id,
            "reverted_intent": ckpt.intent,
        }

    def mark_applied(self, checkpoint_id: str) -> None:
        ckpt = self._load_checkpoint(checkpoint_id)
        if ckpt:
            ckpt.status = "applied"
            self._save_checkpoint(ckpt)
            # Drop the stash since we don't need it anymore
            if ckpt.git_stash_ref:
                subprocess.run(
                    ["git", "stash", "drop", ckpt.git_stash_ref],
                    cwd=self.repo_root, capture_output=True,
                )
            log.info("checkpoint.applied", id=checkpoint_id)

    def list_checkpoints(self) -> list[Checkpoint]:
        index = self._load_index()
        return [self._load_checkpoint(cid) for cid in index if self._load_checkpoint(cid)]

    def latest(self) -> Checkpoint | None:
        checkpoints = self.list_checkpoints()
        active = [c for c in checkpoints if c and c.status == "active"]
        return active[-1] if active else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _snapshot_tf_state(self, ckpt_id: str) -> Path | None:
        """Find and copy all terraform.tfstate files."""
        state_files = list(self.repo_root.rglob("terraform.tfstate"))
        if not state_files:
            return None
        ckpt_state_dir = self.ckpt_dir / ckpt_id
        ckpt_state_dir.mkdir(parents=True, exist_ok=True)
        for sf in state_files:
            rel = sf.relative_to(self.repo_root)
            dest = ckpt_state_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sf, dest)
        return ckpt_state_dir

    def _restore_tf_state(self, snapshot_dir: str) -> bool:
        """Restore terraform state files from snapshot directory."""
        try:
            snap = Path(snapshot_dir)
            for sf in snap.rglob("terraform.tfstate"):
                rel = sf.relative_to(snap)
                dest = self.repo_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sf, dest)
            return True
        except Exception as e:
            log.error("checkpoint.tf_restore_failed", error=str(e))
            return False

    def _git_stash(self, ckpt_id: str, intent: str) -> str | None:
        """Create a named git stash. Returns stash reference."""
        result = subprocess.run(
            ["git", "stash", "push", "-u", "-m", f"cloudforge-ckpt-{ckpt_id}: {intent[:50]}"],
            cwd=self.repo_root, capture_output=True, text=True,
        )
        if result.returncode != 0 or "No local changes" in result.stdout:
            return None
        # Get the stash reference
        list_result = subprocess.run(
            ["git", "stash", "list", "--format=%gd %s"],
            cwd=self.repo_root, capture_output=True, text=True,
        )
        for line in list_result.stdout.splitlines():
            if f"cloudforge-ckpt-{ckpt_id}" in line:
                return line.split()[0]  # stash@{n}
        return "stash@{0}"

    def _git_stash_pop(self, stash_ref: str) -> bool:
        result = subprocess.run(
            ["git", "stash", "pop", stash_ref],
            cwd=self.repo_root, capture_output=True, text=True,
        )
        return result.returncode == 0

    def _save_checkpoint(self, ckpt: Checkpoint) -> None:
        ckpt_file = self.ckpt_dir / f"{ckpt.id}.json"
        ckpt_file.write_text(json.dumps(ckpt.__dict__, indent=2))
        index = self._load_index()
        if ckpt.id not in index:
            index.append(ckpt.id)
            self._index_path.write_text(json.dumps(index, indent=2))

    def _load_checkpoint(self, ckpt_id: str) -> Checkpoint | None:
        ckpt_file = self.ckpt_dir / f"{ckpt_id}.json"
        if not ckpt_file.exists():
            return None
        data = json.loads(ckpt_file.read_text())
        return Checkpoint(**data)

    def _load_index(self) -> list[str]:
        if self._index_path.exists():
            return json.loads(self._index_path.read_text())
        return []
