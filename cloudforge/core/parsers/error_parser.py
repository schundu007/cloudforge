"""
CloudForge Error Parser
Converts raw act/terraform/checkov/tfsec/opa/pint output into
structured PatchRequest objects for the auto-fix loop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from cloudforge.core.agents.orchestrator import TaskType


class ErrorType(str, Enum):
    TERRAFORM_INIT = "terraform_init"
    TERRAFORM_PLAN = "terraform_plan"
    CHECKOV = "checkov"
    TFSEC = "tfsec"
    ACT = "act"
    ACTIONLINT = "actionlint"
    OPA = "opa"
    PINT = "pint"
    UNKNOWN = "unknown"


@dataclass
class Patch:
    file_path: str
    error_type: ErrorType
    message: str
    line_number: int | None = None
    snippet: str | None = None


@dataclass
class PatchRequest:
    agent_type: TaskType
    error_type: ErrorType
    message: str
    patches: list[Patch] = field(default_factory=list)
    checkov_findings: list[dict] = field(default_factory=list)
    priority: int = 1  # 1=critical, 2=high, 3=medium


class ErrorParser:
    """
    Translates raw emulator errors into typed PatchRequest objects.
    The auto-fix loop feeds these to the appropriate sub-agent.
    """

    ERROR_PATTERNS = {
        ErrorType.TERRAFORM_PLAN: [
            r"Error: (.+)\n\s+on (.+) line (\d+)",
            r"│ Error: (.+)",
            r"Error creating (.+): (.+)",
        ],
        ErrorType.CHECKOV: [
            r"Check: (CKV_\w+): \"(.+)\"",
            r"FAILED for resource: (.+)",
        ],
        ErrorType.ACT: [
            r"\[.+\] ❌  (.+)",
            r"Error: (.+)",
        ],
        ErrorType.ACTIONLINT: [
            r"(.+):(\d+):(\d+): (.+) \[(.+)\]",
        ],
        ErrorType.OPA: [
            r"FAIL (.+)",
            r"(\d+) failures",
        ],
        ErrorType.PINT: [
            r"(.+): (.+) \((.+)\)",
        ],
    }

    # Map error type to which sub-agent should fix it
    AGENT_MAP = {
        ErrorType.TERRAFORM_PLAN: TaskType.IAC,
        ErrorType.TERRAFORM_INIT: TaskType.IAC,
        ErrorType.CHECKOV: TaskType.SECURITY,
        ErrorType.TFSEC: TaskType.SECURITY,
        ErrorType.ACT: TaskType.PIPELINE,
        ErrorType.ACTIONLINT: TaskType.PIPELINE,
        ErrorType.OPA: TaskType.SECURITY,
        ErrorType.PINT: TaskType.OBSERVABILITY,
        ErrorType.UNKNOWN: TaskType.IAC,
    }

    def parse(self, errors: list[dict]) -> PatchRequest:
        """Parse list of emulator error dicts into a single PatchRequest."""
        if not errors:
            return PatchRequest(
                agent_type=TaskType.IAC,
                error_type=ErrorType.UNKNOWN,
                message="No errors to parse",
            )

        # Prioritize: checkov/tfsec > tf_plan > act > pint > opa
        priority_order = [
            ErrorType.CHECKOV, ErrorType.TFSEC,
            ErrorType.TERRAFORM_PLAN, ErrorType.TERRAFORM_INIT,
            ErrorType.ACT, ErrorType.ACTIONLINT,
            ErrorType.PINT, ErrorType.OPA,
        ]

        patches: list[Patch] = []
        primary_error: dict | None = None
        primary_type = ErrorType.UNKNOWN

        for error in errors:
            raw_type = error.get("type", "unknown")
            error_type = self._map_type(raw_type)
            message = error.get("message", "")
            file_path = error.get("file", "unknown")
            checkov_findings = error.get("checkov_findings", [])

            patch = Patch(
                file_path=file_path,
                error_type=error_type,
                message=self._clean_message(message),
                line_number=self._extract_line_number(message),
                snippet=self._extract_snippet(message),
            )
            patches.append(patch)

            # Track highest priority error
            try:
                idx = priority_order.index(error_type)
            except ValueError:
                idx = len(priority_order)
            try:
                cur_idx = priority_order.index(primary_type)
            except ValueError:
                cur_idx = len(priority_order)

            if idx < cur_idx:
                primary_error = error
                primary_type = error_type

        agent_type = self.AGENT_MAP.get(primary_type, TaskType.IAC)
        message = primary_error.get("message", "") if primary_error else ""
        checkov_findings = primary_error.get("checkov_findings", []) if primary_error else []

        return PatchRequest(
            agent_type=agent_type,
            error_type=primary_type,
            message=self._clean_message(message),
            patches=patches,
            checkov_findings=checkov_findings,
            priority=1 if primary_type in (ErrorType.CHECKOV, ErrorType.TFSEC) else 2,
        )

    def _map_type(self, raw: str) -> ErrorType:
        mapping = {
            "tf_plan": ErrorType.TERRAFORM_PLAN,
            "terraform_plan": ErrorType.TERRAFORM_PLAN,
            "terraform_init": ErrorType.TERRAFORM_INIT,
            "checkov": ErrorType.CHECKOV,
            "tfsec": ErrorType.TFSEC,
            "act": ErrorType.ACT,
            "actionlint": ErrorType.ACTIONLINT,
            "opa": ErrorType.OPA,
            "pint": ErrorType.PINT,
        }
        return mapping.get(raw.lower(), ErrorType.UNKNOWN)

    def _clean_message(self, msg: str) -> str:
        """Strip ANSI color codes and Terraform box-drawing chars."""
        msg = re.sub(r'\x1b\[[0-9;]*m', '', msg)
        msg = re.sub(r'[│╷╰╭]+', '', msg)
        return msg.strip()[:2000]  # cap at 2000 chars for prompt safety

    def _extract_line_number(self, msg: str) -> int | None:
        m = re.search(r'line (\d+)', msg)
        return int(m.group(1)) if m else None

    def _extract_snippet(self, msg: str) -> str | None:
        lines = msg.splitlines()
        if len(lines) > 3:
            return "\n".join(lines[:5])
        return None
