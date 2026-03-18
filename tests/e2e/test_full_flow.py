"""
CloudForge E2E Tests
Full end-to-end: intent → orchestrator → sub-agents → emulator → diff → PR.
Requires: ANTHROPIC_API_KEY, GITHUB_TOKEN (or mocks).
Run with: pytest tests/e2e/ -m e2e -v
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a minimal mock infra repo."""
    # Init git
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

    # Terraform module
    (tmp_path / "modules" / "networking").mkdir(parents=True)
    (tmp_path / "modules" / "networking" / "main.tf").write_text(
        'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }\n'
    )
    (tmp_path / "modules" / "networking" / "variables.tf").write_text(
        'variable "env" { default = "dev" }\nvariable "project" { default = "test" }\n'
    )

    # GHA workflow
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\non:\n  push:\n    branches: [main]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n    - uses: actions/checkout@v4\n"
    )

    # Stage all files
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


class TestE2EIaCIntent:
    """Test: user asks for a new IaC resource → full loop."""

    async def test_iac_intent_generates_terraform(self, repo: Path):
        """
        Given: a repo with a VPC module
        When: user asks to add a private subnet
        Then: orchestrator generates Terraform, passes checkov/tfsec, produces a diff
        """
        from cloudforge.core.agents.orchestrator import Orchestrator

        # Mock Claude API responses
        classify_resp = MagicMock()
        classify_resp.content = [MagicMock(text="iac")]

        tf_content = '''resource "aws_subnet" "private" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = var.az
  tags = merge(local.common_tags, { Name = "tf-test-priv${var.env}-x7k-subnet" })
}'''
        iac_resp = MagicMock()
        iac_resp.content = [MagicMock(text='{"files":[{"path":"modules/networking/subnet.tf","content":"' +
                                          tf_content.replace('"', '\\"').replace('\n', '\\n') + '"}]}')]

        responses = [classify_resp, iac_resp]
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        orch = Orchestrator(
            repo_root=repo,
            github_token="fake-token",
            anthropic_api_key="fake-key",
        )

        with patch.object(orch.client.messages, "create", side_effect=mock_create):
            with patch.object(orch.agents["iac"]._run_checkov.__self__ if hasattr(orch.agents["iac"]._run_checkov, '__self__') else orch.agents["iac"],
                              "_run_checkov",
                              return_value={"passed": True, "passed_checks": 5, "failed_checks": 0, "critical_findings": []}):
                with patch.object(orch.agents["iac"], "_run_tfsec",
                                  return_value={"passed": True, "findings": [], "total": 0}):
                    with patch.object(orch.emulator, "_run_tf_plan",
                                      new=AsyncMock(return_value={"passed": True, "type": "tf_plan", "summary": "tf plan: +1 ~0 -0"})):

                        events = []
                        async for event in orch.run("Add a private subnet to the networking module"):
                            events.append(event)

        event_types = [e["event"] for e in events]
        assert "classified" in event_types
        assert "agent_done" in event_types
        assert "diff_ready" in event_types

        diff_event = next(e for e in events if e["event"] == "diff_ready")
        assert len(diff_event["files"]) > 0
        assert diff_event["files"][0]["agent"] == "iac"
        assert "aws_subnet" in diff_event["files"][0]["content"]


class TestE2EPipelineIntent:
    """Test: user asks to upgrade pipeline auth to OIDC."""

    async def test_pipeline_intent_generates_oidc_workflow(self, repo: Path):
        from cloudforge.core.agents.orchestrator import Orchestrator

        classify_resp = MagicMock()
        classify_resp.content = [MagicMock(text="pipeline")]

        oidc_yml = """name: Deploy
on:
  push:
    branches: [main]
permissions:
  id-token: write
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - uses: aws-actions/configure-aws-credentials@v4
      with:
        role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}
        aws-region: us-east-1"""

        pipeline_resp = MagicMock()
        pipeline_resp.content = [MagicMock(text='{"filename":"deploy-oidc.yml","content":"' +
                                                 oidc_yml.replace('"', '\\"').replace('\n', '\\n') + '"}')]

        responses = [classify_resp, pipeline_resp]
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            r = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return r

        orch = Orchestrator(repo_root=repo, github_token="fake", anthropic_api_key="fake")

        with patch.object(orch.client.messages, "create", side_effect=mock_create):
            with patch.object(orch.agents["pipeline"], "_run_actionlint",
                              return_value={"passed": True, "output": ""}):
                with patch.object(orch.emulator, "_run_act",
                                  new=AsyncMock(return_value={"passed": True, "type": "act", "summary": "act dry-run passed"})):

                    events = []
                    async for event in orch.run("Add OIDC auth to deploy pipeline, remove static credentials"):
                        events.append(event)

        diff_event = next((e for e in events if e["event"] == "diff_ready"), None)
        assert diff_event is not None
        assert any("oidc" in f["path"].lower() or "id-token" in f["content"]
                   for f in diff_event["files"])
        # Ensure no static credentials generated
        for f in diff_event["files"]:
            assert "AWS_ACCESS_KEY_ID" not in f["content"]
            assert "AWS_SECRET_ACCESS_KEY" not in f["content"]


class TestE2EAutoFixLoop:
    """Test: emulator failure triggers auto-fix."""

    async def test_autofix_triggers_on_emulator_failure(self, repo: Path):
        from cloudforge.core.agents.orchestrator import Orchestrator, MAX_FIX_RETRIES
        from cloudforge.core.emulator.runner import EmulatorResult

        classify_resp = MagicMock()
        classify_resp.content = [MagicMock(text="iac")]

        broken_tf = 'resource "aws_s3_bucket" "bad" { bucket = "test" }'
        fixed_tf = '''resource "aws_s3_bucket" "fixed" {
  bucket = "test"
  force_destroy = false
}
resource "aws_s3_bucket_server_side_encryption_configuration" "fixed" {
  bucket = aws_s3_bucket.fixed.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "AES256" } }
}'''

        iac_resp = MagicMock()
        iac_resp.content = [MagicMock(text='{"files":[{"path":"modules/s3/main.tf","content":"' +
                                          broken_tf.replace('"', '\\"') + '"}]}')]
        fix_resp = MagicMock()
        fix_resp.content = [MagicMock(text='{"files":[{"path":"modules/s3/main.tf","content":"' +
                                          fixed_tf.replace('"', '\\"').replace('\n', '\\n') + '"}]}')]

        responses = [classify_resp, iac_resp, fix_resp]
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            r = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return r

        fail_result = EmulatorResult(
            passed=False,
            summary="checkov failed",
            errors=[{"type": "checkov", "file": "modules/s3/main.tf",
                     "message": "CKV_AWS_19: Ensure S3 bucket has SSE enabled",
                     "checkov_findings": [{"check_id": "CKV_AWS_19", "severity": "HIGH"}]}],
        )
        pass_result = EmulatorResult(passed=True, summary="all checks passed", errors=[])
        emulator_call_count = 0

        async def mock_emulator_run(files):
            nonlocal emulator_call_count
            emulator_call_count += 1
            return fail_result if emulator_call_count == 1 else pass_result

        orch = Orchestrator(repo_root=repo, github_token="fake", anthropic_api_key="fake")

        with patch.object(orch.client.messages, "create", side_effect=mock_create):
            with patch.object(orch.agents["iac"], "_run_checkov",
                              return_value={"passed": True, "critical_findings": []}):
                with patch.object(orch.agents["iac"], "_run_tfsec",
                                  return_value={"passed": True}):
                    with patch.object(orch.emulator, "run", side_effect=mock_emulator_run):
                        events = []
                        async for event in orch.run("Add SSE to S3 bucket"):
                            events.append(event)

        event_types = [e["event"] for e in events]
        assert "emulator_fail" in event_types
        assert "fix_start" in event_types
        assert "emulator_pass" in event_types
        assert "diff_ready" in event_types

        # Should only have needed 1 fix iteration
        diff_event = next(e for e in events if e["event"] == "diff_ready")
        assert diff_event["fix_iterations"] == 1


class TestE2EBackgroundAgent:
    """Test: background agent creates git worktree and runs in isolation."""

    async def test_background_agent_creates_worktree(self, repo: Path):
        from cloudforge.core.agents.background_agent import BackgroundAgent

        agent = BackgroundAgent(repo_root=repo)

        mock_run_result = [
            {"event": "context_loaded", "tf_modules": ["modules/networking"], "workflows": []},
            {"event": "classified", "task_type": "iac"},
            {"event": "diff_ready", "files": [
                {"path": "modules/networking/subnet.tf", "content": "resource ...", "diff": "+resource ...", "agent": "iac"}
            ], "fix_iterations": 0},
        ]

        async def mock_orchestrator_run(intent, target_path=None):
            for event in mock_run_result:
                yield event

        with patch("cloudforge.core.agents.background_agent.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.run = mock_orchestrator_run

            task = await agent.start("Refactor all hardcoded AMI IDs")

        assert task.branch.startswith("cloudforge/bg-")
        assert task.task_id is not None
        # Worktree dir should exist
        assert task.worktree_path.exists()

        # Cleanup
        await agent.cleanup(task.task_id)
