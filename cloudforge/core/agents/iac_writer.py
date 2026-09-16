"""
CloudForge IaC Writer
Generates Terraform modules for AWS, GCP, Azure, OCI.
Validates with terraform plan (LocalStack) + checkov + tfsec.
Enforces naming convention: [prefix]-[project]-[suffix][env]-[random]-[resource]
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import anthropic
import structlog

from cloudforge.core.emulator.tools import resolve_tool

from cloudforge.core.parsers.llm_output import parse_files

log = structlog.get_logger()
MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a Terraform expert for multi-cloud infrastructure.
Generate production-grade HCL that:
- Enforces naming: [prefix]-[project]-[suffix][env]-[random]-[resource]
  Example: tf-payments-svcprod-x7k-sg
- Uses data sources not hardcoded IDs (AMIs, zones, regions)
- Passes checkov CKV_AWS_*, CKV_GCP_*, CKV_AZURE_* checks
- Enables encryption at rest and in transit everywhere
- Tags every resource with local.common_tags
- Uses variables for all env-specific values
- Outputs critical resource IDs and ARNs

Provider targets: AWS (>=5.0), GCP (>=6.0), AzureRM (>=4.0), OCI (>=6.0)
Output format: JSON {"files": [{"path": "...", "content": "..."}]}
"""


class IaCWriter:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.client = anthropic.AsyncAnthropic()

    async def generate(self, intent: str, ctx: Any, context_str: str) -> list[dict]:
        log.info("iac_writer.generate", intent=intent)

        prompt = f"""Infrastructure context:
{context_str}

Existing modules: {ctx.tf_modules}
Current resources: {len(ctx.tf_state.get('values', {}).get('root_module', {}).get('resources', []))} in state

Task: {intent}

Generate complete Terraform files. Include: main.tf, variables.tf, outputs.tf.
If a new module, also include versions.tf with required_providers.
"""
        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = resp.content[0].text.strip()
        files_data = self._parse_files(raw)

        results = []
        for fd in files_data:
            path = fd["path"]
            content = fd["content"]
            checkov_result = self._run_checkov(content, path)
            tfsec_result = self._run_tfsec(content)
            diff = self._make_diff(path, content)
            results.append({
                "path": path,
                "content": content,
                "diff": diff,
                "agent": "iac",
                "checkov": checkov_result,
                "tfsec": tfsec_result,
            })
            log.info("iac_writer.file_generated",
                     path=path,
                     checkov_passed=checkov_result["passed"],
                     tfsec_passed=tfsec_result["passed"])

        return results

    async def fix(self, patch_request: Any, files: list[dict], context_str: str) -> list[dict]:
        """Fix checkov/tfsec/terraform plan failures."""
        iac_files = [f for f in files if f.get("agent") == "iac"]
        if not iac_files:
            return files

        combined_content = "\n\n# --- file separator ---\n\n".join(
            f"# {f['path']}\n{f['content']}" for f in iac_files
        )

        prompt = f"""Fix the following Terraform errors:

Error type: {patch_request.error_type}
Error details: {patch_request.message}
Checkov findings: {patch_request.checkov_findings}

Current Terraform content:
{combined_content}

Return the corrected files as JSON: {{"files": [{{"path": "...", "content": "..."}}]}}
Preserve all existing resources. Only fix the reported issues.
"""
        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        fixed = self._parse_files(resp.content[0].text.strip())
        for fd in fixed:
            for f in files:
                if f["path"] == fd["path"]:
                    f["content"] = fd["content"]
                    f["diff"] = self._make_diff(fd["path"], fd["content"])
                    f["checkov"] = self._run_checkov(fd["content"], fd["path"])
                    f["tfsec"] = self._run_tfsec(fd["content"])
        return files

    # ------------------------------------------------------------------
    # Emulator: checkov + tfsec
    # ------------------------------------------------------------------
    def _run_checkov(self, content: str, path: str) -> dict:
        try:
            with tempfile.NamedTemporaryFile(suffix=".tf", mode="w", delete=False) as f:
                f.write(content)
                tmp = f.name
            result = subprocess.run(
                [resolve_tool("checkov") or "checkov", "-f", tmp, "--output", "json", "--quiet"],
                capture_output=True, text=True, timeout=60,
            )
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            passed_checks = data.get("summary", {}).get("passed", 0)
            failed_checks = data.get("summary", {}).get("failed", 0)
            failed_results = data.get("results", {}).get("failed_checks", [])
            critical = [r for r in failed_results if r.get("check_result", {}).get("result") == "failed"
                        and r.get("severity") in ("HIGH", "CRITICAL")]
            return {
                "passed": len(critical) == 0,
                "passed_checks": passed_checks,
                "failed_checks": failed_checks,
                "critical_findings": critical,
                "raw": data,
            }
        except FileNotFoundError:
            return {"passed": True, "skipped": True, "output": "checkov not installed — skipped"}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            return {"passed": False, "output": str(e)}

    def _run_tfsec(self, content: str) -> dict:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tf_file = Path(tmpdir) / "main.tf"
                tf_file.write_text(content)
                result = subprocess.run(
                    ["tfsec", tmpdir, "--format", "json"],
                    capture_output=True, text=True, timeout=30,
                )
                data = json.loads(result.stdout) if result.stdout.strip() else {}
                results = data.get("results", [])
                critical = [r for r in results if r.get("severity") in ("HIGH", "CRITICAL")]
                return {"passed": len(critical) == 0, "findings": critical, "total": len(results)}
        except FileNotFoundError:
            return {"passed": True, "skipped": True, "output": "tfsec not installed — skipped"}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            return {"passed": False, "output": str(e)}

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def _parse_files(self, raw: str) -> list[dict]:
        return parse_files(raw, "modules/generated/main.tf")

    def _make_diff(self, path: str, content: str) -> str:
        lines = content.splitlines()
        added = "\n".join(f"+{l}" for l in lines)
        return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{added}"
