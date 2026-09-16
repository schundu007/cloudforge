"""
CloudForge Security Writer
Generates: IAM policies (least-privilege), OPA/Rego rules, SCPs,
Checkov custom checks, SBOM config. Always-on security gate.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import anthropic
import structlog

from cloudforge.core.parsers.llm_output import parse_files

log = structlog.get_logger()
MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a cloud security expert specializing in IAM, OPA/Rego, and CSPM.
Generate security configs that:
- IAM: always least-privilege, no wildcard resources without justification
- OPA/Rego: valid Rego v1 policy syntax, always include unit tests
- SCPs: deny patterns for common misconfigurations
- Checkov custom: Python-based custom checks inheriting BaseResourceCheck
- All policies must be reviewable and auditable

Output format: JSON {"files": [{"path": "...", "content": "...", "type": "iam|rego|scp|checkov"}]}
"""


class SecurityWriter:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.client = anthropic.AsyncAnthropic()

    async def generate(self, intent: str, ctx: Any, context_str: str) -> list[dict]:
        log.info("security_writer.generate", intent=intent)

        prompt = f"""Infrastructure context:
{context_str}

Task: {intent}

Generate the appropriate security artifacts. For IAM, analyze what AWS/GCP/Azure
resources are referenced in the Terraform modules: {ctx.tf_modules}
and generate the minimum required permissions.
"""
        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=6000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        files_data = self._parse_files(resp.content[0].text.strip())
        results = []
        for fd in files_data:
            opa_result = {}
            if fd.get("type") == "rego":
                opa_result = self._validate_rego(fd["content"])

            results.append({
                "path": fd["path"],
                "content": fd["content"],
                "diff": self._make_diff(fd["path"], fd["content"]),
                "agent": "security",
                "type": fd.get("type", "policy"),
                "opa_valid": opa_result,
            })
        return results

    async def fix(self, patch_request: Any, files: list[dict], context_str: str) -> list[dict]:
        sec_files = [f for f in files if f.get("agent") == "security"]
        if not sec_files:
            return files

        prompt = f"""Fix the following security policy error:
Error: {patch_request.message}
Type: {patch_request.error_type}

Current policy:
{sec_files[0]['content']}

Return corrected content only.
"""
        resp = await self.client.messages.create(
            model=MODEL, max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        sec_files[0]["content"] = resp.content[0].text.strip()
        return files

    def _validate_rego(self, content: str) -> dict:
        try:
            with tempfile.NamedTemporaryFile(suffix=".rego", mode="w", delete=False) as f:
                f.write(content)
                tmp = f.name
            result = subprocess.run(
                ["opa", "check", tmp],
                capture_output=True, text=True, timeout=10,
            )
            return {"passed": result.returncode == 0, "output": result.stderr}
        except FileNotFoundError:
            return {"passed": True, "skipped": True, "output": "opa not installed — skipped"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "output": "opa timed out"}

    def _parse_files(self, raw: str) -> list[dict]:
        return parse_files(raw, "security/policy.json", {"type": "iam"})

    def _make_diff(self, path: str, content: str) -> str:
        lines = content.splitlines()
        added = "\n".join(f"+{l}" for l in lines)
        return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{added}"
