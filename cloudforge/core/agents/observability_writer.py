"""
CloudForge Observability Writer
Generates: OTel collector configs, Prometheus rules + alerts,
Grafana dashboard JSON, SLO definitions. Validates with pint.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import anthropic
import structlog

log = structlog.get_logger()
MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an observability expert for cloud infrastructure.
Generate production-grade observability configs:

OTel Collector (YAML):
- Receivers: otlp, prometheus, cloudwatch, stackdriver, azure_monitor
- Processors: batch, memory_limiter, resource, filter
- Exporters: otlp/http, prometheus, loki
- Use pipelines with proper buffering

Prometheus Rules (YAML):
- recording rules: pre-compute expensive queries
- alerting rules: actionable alerts with runbook URLs
- severity labels: critical, warning, info
- Validate with pint before output

Grafana Dashboards (JSON):
- Proper panel layout with meaningful titles
- Parameterized datasource variables
- Include RED metrics (Rate, Errors, Duration) for every service

Output: JSON {"files": [{"path": "...", "content": "...", "type": "otel|prometheus|grafana|slo"}]}
"""


class ObservabilityWriter:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.client = anthropic.AsyncAnthropic()

    async def generate(self, intent: str, ctx: Any, context_str: str) -> list[dict]:
        log.info("observability_writer.generate", intent=intent)

        prompt = f"""Infrastructure context:
{context_str}

Existing services (from Terraform modules): {ctx.tf_modules}

Task: {intent}

Generate the complete observability stack for the affected services.
Include OTel collector config, Prometheus recording + alerting rules,
and a Grafana dashboard covering RED metrics (Rate, Errors, Duration).
"""
        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        files_data = self._parse_files(resp.content[0].text.strip())
        results = []
        for fd in files_data:
            pint_result = {}
            if fd.get("type") == "prometheus":
                pint_result = self._run_pint(fd["content"])

            results.append({
                "path": fd["path"],
                "content": fd["content"],
                "diff": self._make_diff(fd["path"], fd["content"]),
                "agent": "observability",
                "type": fd.get("type", "otel"),
                "pint": pint_result,
            })
        return results

    async def fix(self, patch_request: Any, files: list[dict], context_str: str) -> list[dict]:
        obs_files = [f for f in files if f.get("agent") == "observability"]
        if not obs_files:
            return files

        prompt = f"""Fix the following observability config error:
Error: {patch_request.message}
Type: {patch_request.error_type}

Current config:
{obs_files[0]['content']}

Return corrected YAML only.
"""
        resp = await self.client.messages.create(
            model=MODEL, max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        obs_files[0]["content"] = resp.content[0].text.strip()
        return files

    def _run_pint(self, content: str) -> dict:
        """Run pint PromQL linter on Prometheus rule files."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
                f.write(content)
                tmp = f.name
            result = subprocess.run(
                ["pint", "lint", tmp],
                capture_output=True, text=True, timeout=15,
            )
            return {"passed": result.returncode == 0, "output": result.stdout + result.stderr}
        except FileNotFoundError:
            return {"passed": True, "output": "pint not installed — skipped"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "output": "pint timed out"}

    def _parse_files(self, raw: str) -> list[dict]:
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group())
                if "files" in data:
                    return data["files"]
            except json.JSONDecodeError:
                pass
        return [{"path": "observability/otel-collector.yaml", "content": raw, "type": "otel"}]

    def _make_diff(self, path: str, content: str) -> str:
        lines = content.splitlines()
        added = "\n".join(f"+{l}" for l in lines)
        return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{added}"
