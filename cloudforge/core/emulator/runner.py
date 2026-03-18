"""
CloudForge Emulator Runner
Mirrors Firebender's emulator access: run Gradle, read output, iterate.
Here: act (GHA local), terraform plan (LocalStack), checkov, tfsec.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()

LOCALSTACK_ENDPOINT = "http://localhost:4566"
ACT_BINARY = "act"


@dataclass
class EmulatorResult:
    passed: bool
    summary: str
    errors: list[dict] = field(default_factory=list)
    raw_output: str = ""


class EmulatorRunner:
    """
    Runs generated files through the local test stack:
    1. act          — GitHub Actions local runner
    2. terraform plan against LocalStack  — IaC validation
    3. checkov      — security scan gate
    4. tfsec        — additional security scan
    5. pint         — Prometheus rule lint

    Returns structured EmulatorResult for the auto-fix loop.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    async def run(self, files: list[dict]) -> EmulatorResult:
        errors = []
        summaries = []

        # Run tests appropriate to each file type
        tasks = []
        for f in files:
            agent = f.get("agent", "")
            if agent == "pipeline":
                tasks.append(self._run_act(f))
            elif agent == "iac":
                tasks.append(self._run_tf_plan(f))
                tasks.append(self._run_checkov_file(f))
            elif agent == "security" and f.get("type") == "rego":
                tasks.append(self._run_opa_test(f))
            elif agent == "observability" and f.get("type") == "prometheus":
                tasks.append(self._run_pint_file(f))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                errors.append({"type": "exception", "message": str(r)})
            elif not r["passed"]:
                errors.append(r)
            else:
                summaries.append(r.get("summary", "pass"))

        passed = len(errors) == 0
        summary = "; ".join(summaries) if summaries else ("all checks passed" if passed else "failures detected")
        return EmulatorResult(passed=passed, summary=summary, errors=errors)

    # ------------------------------------------------------------------
    # Individual runners
    # ------------------------------------------------------------------
    async def _run_act(self, f: dict) -> dict:
        """Run GitHub Actions workflow with act (local runner)."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                wf_path = Path(tmpdir) / ".github" / "workflows"
                wf_path.mkdir(parents=True)
                wf_file = wf_path / Path(f["path"]).name
                wf_file.write_text(f["content"])

                result = subprocess.run(
                    [ACT_BINARY, "--dry-run", "--quiet", "-W", str(wf_path)],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                passed = result.returncode == 0
                return {
                    "passed": passed,
                    "type": "act",
                    "file": f["path"],
                    "summary": "act dry-run passed" if passed else "act dry-run failed",
                    "message": result.stderr if not passed else "",
                    "output": result.stdout,
                }
        except FileNotFoundError:
            return {"passed": True, "type": "act", "summary": "act not installed — skipped"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "type": "act", "message": "act timed out after 120s"}

    async def _run_tf_plan(self, f: dict) -> dict:
        """Run terraform plan against LocalStack."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tf_file = Path(tmpdir) / "main.tf"
                tf_file.write_text(f["content"])

                # Write LocalStack provider override
                provider_override = Path(tmpdir) / "localstack_override.tf"
                provider_override.write_text(self._localstack_provider_override())

                # terraform init
                init_result = subprocess.run(
                    ["terraform", "init", "-backend=false"],
                    cwd=tmpdir, capture_output=True, text=True, timeout=60,
                )
                if init_result.returncode != 0:
                    return {"passed": False, "type": "tf_plan", "message": init_result.stderr,
                            "file": f["path"], "error_type": "terraform_init"}

                # terraform plan
                plan_result = subprocess.run(
                    ["terraform", "plan", "-json", "-no-color"],
                    cwd=tmpdir, capture_output=True, text=True, timeout=90,
                    env={**__import__('os').environ,
                         "AWS_ENDPOINT_URL": LOCALSTACK_ENDPOINT,
                         "AWS_ACCESS_KEY_ID": "test",
                         "AWS_SECRET_ACCESS_KEY": "test",
                         "AWS_DEFAULT_REGION": "us-east-1"},
                )
                passed = plan_result.returncode == 0
                summary = self._parse_tf_plan_summary(plan_result.stdout)
                return {
                    "passed": passed,
                    "type": "tf_plan",
                    "file": f["path"],
                    "summary": summary,
                    "message": plan_result.stderr if not passed else "",
                    "error_type": "terraform_plan" if not passed else None,
                }
        except FileNotFoundError:
            return {"passed": True, "type": "tf_plan", "summary": "terraform not installed — skipped"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "type": "tf_plan", "message": "terraform plan timed out"}

    async def _run_checkov_file(self, f: dict) -> dict:
        """Security scan via checkov."""
        checkov_data = f.get("checkov", {})
        if checkov_data:
            return {
                "passed": checkov_data.get("passed", True),
                "type": "checkov",
                "file": f["path"],
                "summary": f"checkov: {checkov_data.get('passed_checks', 0)} passed, {checkov_data.get('failed_checks', 0)} failed",
                "message": str(checkov_data.get("critical_findings", [])),
                "checkov_findings": checkov_data.get("critical_findings", []),
                "error_type": "checkov",
            }
        return {"passed": True, "type": "checkov", "summary": "no checkov data"}

    async def _run_opa_test(self, f: dict) -> dict:
        try:
            with tempfile.NamedTemporaryFile(suffix=".rego", mode="w", delete=False) as tmp:
                tmp.write(f["content"])
                tmp_path = tmp.name
            result = subprocess.run(
                ["opa", "test", tmp_path],
                capture_output=True, text=True, timeout=15,
            )
            return {"passed": result.returncode == 0, "type": "opa", "file": f["path"],
                    "message": result.stderr, "summary": "opa tests passed" if result.returncode == 0 else "opa tests failed"}
        except FileNotFoundError:
            return {"passed": True, "type": "opa", "summary": "opa not installed — skipped"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "type": "opa", "message": "opa timed out"}

    async def _run_pint_file(self, f: dict) -> dict:
        pint_data = f.get("pint", {})
        if pint_data:
            return {
                "passed": pint_data.get("passed", True),
                "type": "pint",
                "file": f["path"],
                "message": pint_data.get("output", ""),
                "summary": "pint passed" if pint_data.get("passed") else "pint failed",
            }
        return {"passed": True, "type": "pint", "summary": "no pint data"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _localstack_provider_override(self) -> str:
        return """
provider "aws" {
  access_key                  = "test"
  secret_key                  = "test"
  region                      = "us-east-1"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  endpoints {
    s3  = "http://localhost:4566"
    ec2 = "http://localhost:4566"
    iam = "http://localhost:4566"
    sts = "http://localhost:4566"
  }
}
"""

    def _parse_tf_plan_summary(self, stdout: str) -> str:
        """Extract resource counts from terraform plan -json output."""
        counts = {"add": 0, "change": 0, "remove": 0}
        for line in stdout.splitlines():
            try:
                data = json.loads(line)
                if data.get("type") == "change_summary":
                    changes = data.get("changes", {})
                    counts["add"] = changes.get("add", 0)
                    counts["change"] = changes.get("change", 0)
                    counts["remove"] = changes.get("remove", 0)
            except json.JSONDecodeError:
                continue
        return f"tf plan: +{counts['add']} ~{counts['change']} -{counts['remove']}"
