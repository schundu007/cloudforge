"""
CloudForge Emulator Runner
Mirrors Firebender's emulator access: run Gradle, read output, iterate.
Here: act (GHA local), terraform plan (LocalStack), checkov, tfsec.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from cloudforge.core.emulator.tools import resolve_tool

log = structlog.get_logger()

# Overridable so a deployed API can reach a LocalStack service over the
# platform's private network (e.g. http://localstack.railway.internal:4566).
LOCALSTACK_ENDPOINT = os.getenv("LOCALSTACK_ENDPOINT", "http://localhost:4566")
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
        # Terraform files must be planned together: main.tf alone cannot resolve
        # variables declared in variables.tf, so per-file plans always failed
        # with "No value for required variable".
        iac_files = [f for f in files if f.get("agent") == "iac"]
        if iac_files:
            tasks.append(self._run_tf_plan_group(iac_files))

        for f in files:
            agent = f.get("agent", "")
            if agent == "pipeline":
                tasks.append(self._run_act(f))
            elif agent == "iac":
                tasks.append(self._run_checkov_file(f))
            elif agent == "security" and f.get("type") == "rego":
                tasks.append(self._run_opa_test(f))
            elif agent == "observability" and f.get("type") == "prometheus":
                tasks.append(self._run_pint_file(f))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        skipped = []
        for r in results:
            if isinstance(r, Exception):
                errors.append({"type": "exception", "message": str(r)})
            elif not r["passed"]:
                errors.append(r)
            elif r.get("skipped"):
                skipped.append(r.get("type", "tool"))
            else:
                summaries.append(r.get("summary", "pass"))

        passed = len(errors) == 0
        if not tasks:
            summary = "no checks ran — no validator applies to these files"
        elif summaries:
            summary = "; ".join(summaries)
        elif passed:
            # Everything that "passed" was actually skipped: say so rather than
            # reporting a green run nobody verified.
            summary = "no checks ran — not installed: " + ", ".join(skipped) if skipped else "all checks passed"
        else:
            summary = "failures detected"
        if skipped and summaries:
            summary += f" (skipped, not installed: {', '.join(skipped)})"
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
            return {"passed": True, "skipped": True, "type": "act", "summary": "act not installed — skipped"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "type": "act", "message": "act timed out after 120s"}

    async def _run_tf_plan_group(self, files: list[dict]) -> dict:
        """Plan every generated Terraform file together, as one module."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                for f in files:
                    # Flatten to basenames so a module lands in one directory.
                    name = Path(f["path"]).name
                    if not name.endswith(".tf"):
                        continue
                    (tmp / name).write_text(f.get("content", ""))

                (tmp / "localstack_override.tf").write_text(self._localstack_provider_override())

                # Required variables have no values in an emulator run; supply
                # placeholders so the plan exercises the resources themselves.
                tfvars = self._placeholder_tfvars(tmp)
                if tfvars:
                    (tmp / "cloudforge.auto.tfvars.json").write_text(json.dumps(tfvars, indent=2))

                paths = [f["path"] for f in files]
                init_result = subprocess.run(
                    [resolve_tool("terraform") or "terraform", "init", "-backend=false", "-input=false"],
                    cwd=tmpdir, capture_output=True, text=True, timeout=120,
                )
                if init_result.returncode != 0:
                    return {"passed": False, "type": "tf_plan", "files": paths,
                            "message": init_result.stderr.strip() or init_result.stdout.strip()[:2000],
                            "error_type": "terraform_init"}

                plan_result = subprocess.run(
                    [resolve_tool("terraform") or "terraform", "plan", "-json", "-no-color", "-input=false"],
                    cwd=tmpdir, capture_output=True, text=True, timeout=150,
                    env={**os.environ,
                         "AWS_ENDPOINT_URL": LOCALSTACK_ENDPOINT,
                         "AWS_ACCESS_KEY_ID": "test",
                         "AWS_SECRET_ACCESS_KEY": "test",
                         "AWS_DEFAULT_REGION": "us-east-1"},
                )
                passed = plan_result.returncode == 0
                message = "" if passed else (
                    self._extract_tf_json_diagnostics(plan_result.stdout)
                    or plan_result.stderr.strip()
                    or plan_result.stdout.strip()[:2000]
                    or f"terraform plan exited {plan_result.returncode} with no diagnostics"
                )
                return {
                    "passed": passed,
                    "type": "tf_plan",
                    "files": paths,
                    "file": paths[0] if paths else None,
                    "summary": self._parse_tf_plan_summary(plan_result.stdout),
                    "message": message,
                    "error_type": "terraform_plan",
                }
        except FileNotFoundError:
            return {"passed": True, "skipped": True, "type": "tf_plan",
                    "summary": "terraform not installed — skipped"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "type": "tf_plan", "message": "terraform plan timed out"}

    @staticmethod
    def _placeholder_tfvars(tf_dir: Path) -> dict:
        """Placeholder values for variables that declare no default."""
        try:
            import hcl2
        except ImportError:
            return {}

        defaults = {
            "string": "cloudforge-placeholder",
            "number": 1,
            "bool": False,
            "list": [],
            "set": [],
            "map": {},
            "object": {},
            "any": "cloudforge-placeholder",
        }
        values: dict = {}
        for tf_file in sorted(tf_dir.glob("*.tf")):
            try:
                with tf_file.open() as fh:
                    parsed = hcl2.load(fh)
            except Exception:
                continue
            for block in parsed.get("variable", []) or []:
                for name, spec in (block or {}).items():
                    if not isinstance(spec, dict) or "default" in spec:
                        continue
                    raw_type = str(spec.get("type", "string")).lower()
                    kind = next((k for k in defaults if k in raw_type), "string")
                    values[name] = defaults[kind]
        return values

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
                    [resolve_tool("terraform") or "terraform", "init", "-backend=false"],
                    cwd=tmpdir, capture_output=True, text=True, timeout=60,
                )
                if init_result.returncode != 0:
                    return {"passed": False, "type": "tf_plan", "message": init_result.stderr,
                            "file": f["path"], "error_type": "terraform_init"}

                # terraform plan
                plan_result = subprocess.run(
                    [resolve_tool("terraform") or "terraform", "plan", "-json", "-no-color"],
                    cwd=tmpdir, capture_output=True, text=True, timeout=90,
                    env={**__import__('os').environ,
                         "AWS_ENDPOINT_URL": LOCALSTACK_ENDPOINT,
                         "AWS_ACCESS_KEY_ID": "test",
                         "AWS_SECRET_ACCESS_KEY": "test",
                         "AWS_DEFAULT_REGION": "us-east-1"},
                )
                passed = plan_result.returncode == 0
                summary = self._parse_tf_plan_summary(plan_result.stdout)
                if not passed:
                    # `terraform plan -json` streams diagnostics on STDOUT; stderr
                    # is usually empty. Reporting stderr handed the auto-fix loop
                    # an empty message and it iterated blind until escalation.
                    plan_error = self._extract_tf_json_diagnostics(plan_result.stdout) \
                        or plan_result.stderr.strip() \
                        or plan_result.stdout.strip()[:2000] \
                        or f"terraform plan exited {plan_result.returncode} with no diagnostics"
                return {
                    "passed": passed,
                    "type": "tf_plan",
                    "file": f["path"],
                    "summary": summary,
                    "message": plan_error if not passed else "",
                    "error_type": "terraform_plan" if not passed else None,
                }
        except FileNotFoundError:
            return {"passed": True, "skipped": True, "type": "tf_plan", "summary": "terraform not installed — skipped"}
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
                [resolve_tool("opa") or "opa", "test", tmp_path],
                capture_output=True, text=True, timeout=15,
            )
            return {"passed": result.returncode == 0, "type": "opa", "file": f["path"],
                    "message": result.stderr, "summary": "opa tests passed" if result.returncode == 0 else "opa tests failed"}
        except FileNotFoundError:
            return {"passed": True, "skipped": True, "type": "opa", "summary": "opa not installed — skipped"}
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


    @staticmethod
    def _extract_tf_json_diagnostics(stdout: str) -> str:
        """Pull human-readable errors out of `terraform plan -json` output."""
        messages: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("@level") != "error":
                continue
            diag = rec.get("diagnostic") or {}
            parts = [diag.get("summary") or rec.get("@message") or ""]
            if diag.get("detail"):
                parts.append(diag["detail"])
            rng = diag.get("range") or {}
            if rng.get("filename"):
                start = (rng.get("start") or {}).get("line")
                parts.append(f"(at {rng['filename']}" + (f" line {start})" if start else ")"))
            text = " ".join(p for p in parts if p).strip()
            if text and text not in messages:
                messages.append(text)
        return "\n".join(messages)

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
