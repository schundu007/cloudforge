"""
Shared core types.

Lives at the bottom of the dependency graph: this module must not import from
any other cloudforge module, so both the orchestrator and the parsers can
depend on it without forming an import cycle.
"""
from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    PIPELINE = "pipeline"
    IAC = "iac"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    MULTI = "multi"
