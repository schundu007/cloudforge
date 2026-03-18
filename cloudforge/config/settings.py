"""
CloudForge Configuration
Loaded from cloudforge.yaml in repo root.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class HookConfig(BaseModel):
    enabled: bool = True
    block_on: list[str] = Field(default_factory=lambda: ["HIGH", "CRITICAL"])
    monthly_delta_usd: int = 500
    schedule: str = "0 */6 * * *"
    sources: list[str] = Field(default_factory=lambda: ["cloudwatch", "gcp_logs"])
    tf_plan_comment: bool = True


class HooksConfig(BaseModel):
    pre_commit: HookConfig = Field(default_factory=HookConfig, alias="pre-commit")
    pr_opened: HookConfig = Field(default_factory=HookConfig, alias="pr-opened")
    cost_threshold: HookConfig = Field(default_factory=HookConfig, alias="cost-threshold")
    drift: HookConfig = Field(default_factory=HookConfig)
    log_error: HookConfig = Field(default_factory=HookConfig, alias="log-error")
    deploy_prod: HookConfig = Field(default_factory=HookConfig, alias="deploy:prod")

    class Config:
        populate_by_name = True


class SecurityConfig(BaseModel):
    always_run_checkov: bool = True
    always_run_tfsec: bool = True
    block_on_critical: bool = True
    scrub_secrets_from_context: bool = True
    iam_least_privilege: bool = True


class EmulatorConfig(BaseModel):
    localstack_endpoint: str = "http://localhost:4566"
    act_binary: str = "act"
    max_fix_retries: int = 5
    tf_plan_timeout_seconds: int = 120
    act_timeout_seconds: int = 300


class CloudConfig(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ["aws", "gcp", "azure", "oci"])
    aws_region: str = "us-east-1"
    gcp_project_id: str = ""
    azure_subscription_id: str = ""


class NamingConfig(BaseModel):
    convention: str = "[prefix]-[project]-[suffix][env]-[random]-[resource]"
    example: str = "tf-payments-svcprod-x7k-sg"
    prefix: str = "tf"


class CloudForgeConfig(BaseModel):
    version: str = "1"
    project: str = "my-project"
    naming: NamingConfig = Field(default_factory=NamingConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    emulator: EmulatorConfig = Field(default_factory=EmulatorConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)


def load_config(repo_root: Path) -> CloudForgeConfig:
    config_file = repo_root / "cloudforge.yaml"
    if config_file.exists():
        data = yaml.safe_load(config_file.read_text()) or {}
        return CloudForgeConfig.model_validate(data)
    return CloudForgeConfig()


def write_default_config(repo_root: Path) -> Path:
    """Write a default cloudforge.yaml to the repo root."""
    config_path = repo_root / "cloudforge.yaml"
    config_path.write_text(DEFAULT_CONFIG_YAML)
    return config_path


DEFAULT_CONFIG_YAML = """\
version: "1"
project: my-project

naming:
  convention: "[prefix]-[project]-[suffix][env]-[random]-[resource]"
  prefix: tf

cloud:
  platforms: [aws, gcp, azure, oci]
  aws_region: us-east-1

security:
  always_run_checkov: true
  always_run_tfsec: true
  block_on_critical: true
  scrub_secrets_from_context: true
  iam_least_privilege: true

emulator:
  localstack_endpoint: http://localhost:4566
  act_binary: act
  max_fix_retries: 5
  tf_plan_timeout_seconds: 120

hooks:
  pre-commit:
    enabled: true
    block_on: [HIGH, CRITICAL]

  pr-opened:
    enabled: true
    tf_plan_comment: true

  cost-threshold:
    enabled: true
    monthly_delta_usd: 500

  drift:
    enabled: true
    schedule: "0 */6 * * *"   # every 6 hours

  deploy:prod:
    enabled: true

  log-error:
    enabled: true
    sources: [cloudwatch, gcp_logs, azure_monitor]
"""
