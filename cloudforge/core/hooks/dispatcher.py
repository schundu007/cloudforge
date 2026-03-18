"""
CloudForge Hooks System
Mirrors Firebender's hooks for automated workflows and custom agent behaviors.
Triggers: pre-commit, pr-opened, label, cost-threshold, drift, deploy, log-error
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


class HookDispatcher:
    """
    Routes lifecycle events to the appropriate CloudForge agent action.
    Configured via cloudforge-hooks.yaml in the repo root.
    """

    HOOK_INTENTS = {
        "pre-commit": "Scan staged Terraform files with checkov and tfsec. Auto-fix all HIGH and CRITICAL findings.",
        "pr-opened": "Run terraform plan against LocalStack for changed modules. Post summary as PR comment.",
        "label:cf-add-obs": "Generate OTel + Prometheus + Grafana observability for services modified in this PR.",
        "label:cf-add-security": "Generate IAM least-privilege policy and OPA rules for services in this PR.",
        "cost-threshold": "Analyze Infracost plan output and generate a cost breakdown report.",
        "drift": "Detect Terraform state drift and generate a reconciliation diff.",
        "deploy:prod": "Run security gate: checkov + tfsec + OPA + SBOM generation before allowing deploy.",
        "log-error": "Analyze CloudWatch/GCP log error spike, identify root cause, generate fix PR.",
    }

    async def dispatch(self, hook_type: str, payload: dict) -> str:
        run_id = str(uuid.uuid4())
        intent = self._resolve_intent(hook_type, payload)

        log.info("hook.dispatch", hook_type=hook_type, run_id=run_id, intent=intent[:80])

        handler = getattr(self, f"_handle_{hook_type.replace('-', '_').replace(':', '_')}", None)
        if handler:
            await handler(run_id, payload, intent)
        else:
            await self._handle_generic(run_id, payload, intent)

        return run_id

    def _resolve_intent(self, hook_type: str, payload: dict) -> str:
        base = self.HOOK_INTENTS.get(hook_type, f"Handle {hook_type} event")
        # Enrich intent with payload context
        if hook_type == "pr-opened":
            pr_title = payload.get("pull_request", {}).get("title", "")
            files = payload.get("files_changed", [])
            return f"{base} PR: '{pr_title}'. Changed files: {files}"
        if hook_type == "log-error":
            service = payload.get("service", "unknown")
            error_msg = payload.get("error_message", "")
            return f"{base} Service: {service}. Error: {error_msg[:200]}"
        return base

    async def _handle_pre_commit(self, run_id: str, payload: dict, intent: str) -> None:
        """Runs synchronously as a pre-commit hook — must be fast."""
        staged_files = payload.get("staged_files", [])
        tf_files = [f for f in staged_files if f.endswith(".tf")]
        if not tf_files:
            log.info("hook.pre_commit.no_tf_files", run_id=run_id)
            return
        log.info("hook.pre_commit.scanning", files=tf_files, run_id=run_id)
        # In production: call SecurityWriter directly, block commit on critical findings

    async def _handle_drift(self, run_id: str, payload: dict, intent: str) -> None:
        """Detect drift and auto-open a reconciliation PR."""
        workspace = payload.get("workspace", "default")
        log.info("hook.drift.detected", workspace=workspace, run_id=run_id)
        # In production: trigger orchestrator.run(intent) and push PR

    async def _handle_log_error(self, run_id: str, payload: dict, intent: str) -> None:
        """Auto-diagnose a log error spike and generate a fix."""
        service = payload.get("service", "unknown")
        log.info("hook.log_error.diagnosing", service=service, run_id=run_id)
        # In production: fetch last N log lines from CloudWatch/GCP → orchestrator.run(intent)

    async def _handle_generic(self, run_id: str, payload: dict, intent: str) -> None:
        log.info("hook.generic", run_id=run_id, intent=intent[:80])
