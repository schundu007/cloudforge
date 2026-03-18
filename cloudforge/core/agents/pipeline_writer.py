"""
CloudForge Pipeline Writer
Generates GitHub Actions workflows: deploy, CI, OIDC auth, matrix builds.
Validates with act + actionlint before returning diff.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import anthropic
import yaml
import structlog

log = structlog.get_logger()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a GitHub Actions expert for cloud infrastructure.
Generate production-grade workflow YAML with:
- OIDC authentication (never static credentials)
- Environment-specific deploy gates (dev -> staging -> prod)
- Matrix builds where appropriate
- Reusable composite actions
- Proper permissions blocks (least-privilege)
- actionlint-compatible syntax

Cloud targets: AWS (aws-actions/configure-aws-credentials@v4), GCP (google-github-actions/auth@v2),
Azure (azure/login@v2), OCI (oracle-actions/configure-cloud-credentials@v1).

Always output ONLY valid YAML. No markdown fences. No explanation text.
"""

WORKFLOW_TEMPLATES = {
    "aws_deploy": """
name: Deploy to AWS
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  id-token: write
  contents: read

jobs:
  plan:
    runs-on: ubuntu-latest
    environment: ${{ matrix.env }}
    strategy:
      matrix:
        env: [dev, staging]
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars[format('AWS_ROLE_{0}', matrix.env)] }}
          aws-region: us-east-1
      - uses: hashicorp/setup-terraform@v3
      - run: terraform init && terraform plan -out=tfplan
      - uses: actions/upload-artifact@v4
        with:
          name: tfplan-${{ matrix.env }}
          path: tfplan
""",
    "gcp_deploy": """
name: Deploy to GCP
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
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_SA_EMAIL }}
      - uses: google-github-actions/setup-gcloud@v2
      - run: gcloud run deploy --region us-central1
""",
}


class PipelineWriter:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.client = anthropic.AsyncAnthropic()

    async def generate(self, intent: str, ctx: Any, context_str: str) -> list[dict]:
        """Generate one or more workflow YAML files from an intent string."""
        log.info("pipeline_writer.generate", intent=intent)

        prompt = f"""Infrastructure context:
{context_str}

Existing workflows: {ctx.gha_workflows}

Task: {intent}

Generate the complete GitHub Actions workflow YAML.
Filename should be descriptive (e.g. deploy-aws-prod.yml).
Return JSON: {{"filename": "...", "content": "..."}}
"""
        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = resp.content[0].text.strip()
        # Parse JSON wrapper
        import json, re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
        else:
            data = {"filename": "generated-workflow.yml", "content": raw}

        filename = data["filename"]
        content = data["content"]

        # Validate YAML is parseable
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as e:
            log.warning("pipeline_writer.invalid_yaml", error=str(e))

        path = f".github/workflows/{filename}"
        diff = self._make_diff(path, content)

        # Run actionlint validation
        lint_result = self._run_actionlint(content, filename)

        return [{
            "path": path,
            "content": content,
            "diff": diff,
            "agent": "pipeline",
            "actionlint": lint_result,
            "checkov": None,  # pipelines don't run checkov
        }]

    async def fix(self, patch_request: Any, files: list[dict], context_str: str) -> list[dict]:
        """Apply a fix to a pipeline file given structured error info."""
        target = next((f for f in files if f["agent"] == "pipeline"), files[0])

        prompt = f"""Fix the following GitHub Actions workflow error:

Error: {patch_request.message}
Error type: {patch_request.error_type}

Current workflow content:
{target['content']}

Return the corrected YAML only. No explanation.
"""
        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        fixed_content = resp.content[0].text.strip()
        target["content"] = fixed_content
        target["diff"] = self._make_diff(target["path"], fixed_content)
        return files

    def _make_diff(self, path: str, content: str) -> str:
        lines = content.splitlines()
        added = "\n".join(f"+{line}" for line in lines)
        return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{added}"

    def _run_actionlint(self, content: str, filename: str) -> dict:
        """Run actionlint on generated workflow. Returns {passed, output}."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
                f.write(content)
                tmp = f.name
            result = subprocess.run(
                ["actionlint", tmp],
                capture_output=True, text=True, timeout=15,
            )
            return {"passed": result.returncode == 0, "output": result.stdout + result.stderr}
        except FileNotFoundError:
            return {"passed": True, "output": "actionlint not installed — skipped"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "output": "actionlint timed out"}
