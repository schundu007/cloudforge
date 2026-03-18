"""
CloudForge Integration Tests
Tests that require real file system and subprocess calls.
Mark slow tests with @pytest.mark.integration
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest


pytestmark = pytest.mark.integration


# ── Pipeline Writer Integration ──────────────────────────────────────
class TestPipelineWriterIntegration:
    @pytest.fixture
    def tmp_repo(self, tmp_path):
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        return tmp_path

    @pytest.mark.asyncio
    async def test_generate_aws_deploy_workflow(self, tmp_repo):
        from cloudforge.core.agents.pipeline_writer import PipelineWriter
        from cloudforge.core.context.store import ContextStore

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='''{"filename": "deploy-aws.yml", "content": "name: Deploy to AWS\\non:\\n  push:\\n    branches: [main]\\npermissions:\\n  id-token: write\\n  contents: read\\njobs:\\n  deploy:\\n    runs-on: ubuntu-latest\\n    steps:\\n    - uses: actions/checkout@v4\\n    - uses: aws-actions/configure-aws-credentials@v4\\n      with:\\n        role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}\\n        aws-region: us-east-1"}''')]

        store = ContextStore(tmp_repo)
        ctx = store.refresh()
        writer = PipelineWriter(tmp_repo)

        with patch.object(writer.client.messages, "create", new=AsyncMock(return_value=mock_response)):
            files = await writer.generate("Deploy to AWS with OIDC", ctx, store.as_prompt_context())

        assert len(files) == 1
        assert files[0]["agent"] == "pipeline"
        assert "role-to-assume" in files[0]["content"]
        assert "AWS_ACCESS_KEY_ID" not in files[0]["content"]  # no static creds
        assert files[0]["path"].startswith(".github/workflows/")

    @pytest.mark.asyncio
    async def test_fix_pipeline_error(self, tmp_repo):
        from cloudforge.core.agents.pipeline_writer import PipelineWriter
        from cloudforge.core.parsers.error_parser import PatchRequest, ErrorType
        from cloudforge.core.agents.orchestrator import TaskType

        fixed_yaml = "name: Fixed\non:\n  push:\n    branches: [main]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    permissions:\n      id-token: write\n    steps:\n    - uses: actions/checkout@v4"
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=fixed_yaml)]

        writer = PipelineWriter(tmp_repo)
        patch_req = PatchRequest(
            agent_type=TaskType.PIPELINE,
            error_type=ErrorType.ACT,
            message="Missing permissions block",
        )
        existing_files = [{
            "path": ".github/workflows/deploy.yml",
            "content": "name: Broken",
            "agent": "pipeline",
            "diff": "",
        }]

        with patch.object(writer.client.messages, "create", new=AsyncMock(return_value=mock_response)):
            result = await writer.fix(patch_req, existing_files, "{}")

        assert "permissions" in result[0]["content"]


# ── IaC Writer Integration ───────────────────────────────────────────
class TestIaCWriterIntegration:
    @pytest.mark.asyncio
    async def test_generate_terraform_module(self, tmp_path):
        from cloudforge.core.agents.iac_writer import IaCWriter
        from cloudforge.core.context.store import ContextStore

        tf_content = '''resource "aws_subnet" "private" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = var.az
  tags = merge(local.common_tags, { Name = "tf-payments-privdev-x7k-subnet" })
}'''
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "files": [{"path": "modules/networking/main.tf", "content": tf_content}]
        }))]

        store = ContextStore(tmp_path)
        ctx = store.refresh()
        writer = IaCWriter(tmp_path)

        with patch.object(writer.client.messages, "create", new=AsyncMock(return_value=mock_response)):
            with patch.object(writer, "_run_checkov", return_value={"passed": True, "passed_checks": 5, "failed_checks": 0, "critical_findings": []}):
                with patch.object(writer, "_run_tfsec", return_value={"passed": True, "findings": [], "total": 0}):
                    files = await writer.generate("Add private subnet", ctx, store.as_prompt_context())

        assert len(files) == 1
        assert files[0]["agent"] == "iac"
        assert "aws_subnet" in files[0]["content"]
        assert files[0]["checkov"]["passed"] is True

    @pytest.mark.asyncio
    async def test_naming_convention_in_generated_tf(self, tmp_path):
        """Verify naming convention is in generated content."""
        from cloudforge.core.agents.iac_writer import IaCWriter
        from cloudforge.core.context.store import ContextStore

        # Convention: [prefix]-[project]-[suffix][env]-[random]-[resource]
        tf_with_convention = 'Name = "tf-payments-svcprod-x7k-sg"'
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "files": [{"path": "modules/sg/main.tf", "content": tf_with_convention}]
        }))]

        store = ContextStore(tmp_path)
        ctx = store.refresh()
        writer = IaCWriter(tmp_path)

        with patch.object(writer.client.messages, "create", new=AsyncMock(return_value=mock_response)):
            with patch.object(writer, "_run_checkov", return_value={"passed": True, "critical_findings": []}):
                with patch.object(writer, "_run_tfsec", return_value={"passed": True}):
                    files = await writer.generate("Add security group", ctx, "{}")

        # Naming convention should match tf-[project]-[suffix][env]-[random]-[resource]
        assert "tf-payments" in files[0]["content"]


# ── Emulator Runner Integration ──────────────────────────────────────
class TestEmulatorRunnerIntegration:
    @pytest.mark.asyncio
    async def test_run_passes_on_clean_files(self, tmp_path):
        from cloudforge.core.emulator.runner import EmulatorRunner

        runner = EmulatorRunner(tmp_path)
        files = [{
            "path": "modules/main.tf",
            "content": 'resource "null_resource" "test" {}',
            "agent": "iac",
            "checkov": {"passed": True, "passed_checks": 3, "failed_checks": 0, "critical_findings": []},
            "tfsec": {"passed": True},
        }]

        result = await runner.run(files)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_run_fails_on_checkov_critical(self, tmp_path):
        from cloudforge.core.emulator.runner import EmulatorRunner

        runner = EmulatorRunner(tmp_path)
        files = [{
            "path": "modules/main.tf",
            "content": 'resource "aws_s3_bucket" "bad" {}',
            "agent": "iac",
            "checkov": {
                "passed": False,
                "passed_checks": 1,
                "failed_checks": 3,
                "critical_findings": [{"check_id": "CKV_AWS_18", "severity": "HIGH"}],
            },
            "tfsec": {"passed": True},
        }]

        result = await runner.run(files)
        assert result.passed is False
        assert len(result.errors) > 0
        assert result.errors[0]["type"] == "checkov"


import json
