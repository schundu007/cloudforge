"""
CloudForge Unit Tests
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from cloudforge.core.parsers.error_parser import ErrorParser, ErrorType, PatchRequest
from cloudforge.core.agents.orchestrator import TaskType
from cloudforge.config.settings import load_config, CloudForgeConfig


# ── Error Parser Tests ──────────────────────────────────────────────
class TestErrorParser:
    def setup_method(self):
        self.parser = ErrorParser()

    def test_parse_checkov_error(self):
        errors = [{
            "type": "checkov",
            "file": "modules/vpc/main.tf",
            "message": "CKV_AWS_119: Ensure DynamoDB Tables are encrypted",
            "checkov_findings": [{"check_id": "CKV_AWS_119", "severity": "HIGH"}],
        }]
        result = self.parser.parse(errors)
        assert result.error_type == ErrorType.CHECKOV
        assert result.agent_type == TaskType.SECURITY
        assert result.priority == 1

    def test_parse_terraform_plan_error(self):
        errors = [{
            "type": "tf_plan",
            "file": "modules/networking/main.tf",
            "message": "Error: Invalid reference │ on main.tf line 42 │ var.nonexistent",
        }]
        result = self.parser.parse(errors)
        assert result.error_type == ErrorType.TERRAFORM_PLAN
        assert result.agent_type == TaskType.IAC

    def test_parse_act_error(self):
        errors = [{
            "type": "act",
            "file": ".github/workflows/deploy.yml",
            "message": "Error: GITHUB_TOKEN not set",
        }]
        result = self.parser.parse(errors)
        assert result.error_type == ErrorType.ACT
        assert result.agent_type == TaskType.PIPELINE

    def test_parse_empty_errors(self):
        result = self.parser.parse([])
        assert result.agent_type == TaskType.IAC
        assert result.error_type == ErrorType.UNKNOWN

    def test_priority_checkov_over_act(self):
        errors = [
            {"type": "act", "message": "workflow failed", "file": "deploy.yml"},
            {"type": "checkov", "message": "CKV_AWS_119 failed", "file": "main.tf",
             "checkov_findings": [{"severity": "HIGH"}]},
        ]
        result = self.parser.parse(errors)
        # checkov should win over act
        assert result.error_type == ErrorType.CHECKOV

    def test_clean_message_strips_ansi(self):
        msg = "\x1b[31mError\x1b[0m: something failed"
        cleaned = self.parser._clean_message(msg)
        assert "\x1b" not in cleaned
        assert "Error" in cleaned

    def test_extract_line_number(self):
        msg = "Error on line 42: invalid reference"
        assert self.parser._extract_line_number(msg) == 42

    def test_message_capped_at_2000_chars(self):
        long_msg = "x" * 5000
        cleaned = self.parser._clean_message(long_msg)
        assert len(cleaned) <= 2000


# ── Config Tests ────────────────────────────────────────────────────
class TestConfig:
    def test_default_config(self, tmp_path):
        cfg = load_config(tmp_path)
        assert isinstance(cfg, CloudForgeConfig)
        assert "aws" in cfg.cloud.platforms
        assert cfg.security.always_run_checkov is True
        assert cfg.emulator.max_fix_retries == 5

    def test_load_custom_config(self, tmp_path):
        config_file = tmp_path / "cloudforge.yaml"
        config_file.write_text("""
version: "1"
project: payments
naming:
  prefix: pay
cloud:
  platforms: [aws]
  aws_region: eu-west-1
security:
  block_on_critical: false
emulator:
  max_fix_retries: 3
""")
        cfg = load_config(tmp_path)
        assert cfg.project == "payments"
        assert cfg.naming.prefix == "pay"
        assert cfg.cloud.aws_region == "eu-west-1"
        assert cfg.security.block_on_critical is False
        assert cfg.emulator.max_fix_retries == 3

    def test_naming_convention_default(self, tmp_path):
        cfg = load_config(tmp_path)
        assert "[prefix]" in cfg.naming.convention
        assert cfg.naming.example == "tf-payments-svcprod-x7k-sg"


# ── Context Store Tests ─────────────────────────────────────────────
class TestContextStore:
    def test_scan_repo_tree(self, tmp_path):
        from cloudforge.core.context.store import ContextStore
        (tmp_path / "modules" / "vpc").mkdir(parents=True)
        (tmp_path / "modules" / "vpc" / "main.tf").write_text('resource "aws_vpc" "main" {}')
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / ".github" / "workflows" / "deploy.yml").write_text("name: Deploy")

        store = ContextStore(tmp_path)
        ctx = store.refresh()

        assert any("main.tf" in k for k in ctx.tree)
        assert len(ctx.tf_modules) == 1
        assert len(ctx.gha_workflows) == 1

    def test_prompt_context_serializes(self, tmp_path):
        from cloudforge.core.context.store import ContextStore
        store = ContextStore(tmp_path)
        store.refresh()
        prompt_ctx = store.as_prompt_context()
        data = json.loads(prompt_ctx)
        assert "repo_root" in data
        assert "tf_modules" in data

    def test_no_tf_state_doesnt_crash(self, tmp_path):
        from cloudforge.core.context.store import ContextStore
        store = ContextStore(tmp_path)
        ctx = store.refresh()
        assert ctx.tf_state == {}


# ── Checkpoint Tests ────────────────────────────────────────────────
class TestCheckpointManager:
    def test_create_and_list(self, tmp_path):
        from cloudforge.core.emulator.checkpoint import CheckpointManager
        # Init a git repo so stash works
        subprocess_result = MagicMock()
        subprocess_result.returncode = 0
        subprocess_result.stdout = ""

        with patch("subprocess.run", return_value=subprocess_result):
            mgr = CheckpointManager(tmp_path)
            ckpt = mgr.create("test intent")
            assert ckpt.id is not None
            assert ckpt.intent == "test intent"
            assert ckpt.status == "active"

            checkpoints = mgr.list_checkpoints()
            assert len(checkpoints) == 1
            assert checkpoints[0].id == ckpt.id

    def test_mark_applied(self, tmp_path):
        from cloudforge.core.emulator.checkpoint import CheckpointManager
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            mgr = CheckpointManager(tmp_path)
            ckpt = mgr.create("another intent")
            mgr.mark_applied(ckpt.id)
            loaded = mgr._load_checkpoint(ckpt.id)
            assert loaded.status == "applied"
