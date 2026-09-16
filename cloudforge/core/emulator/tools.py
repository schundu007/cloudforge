"""
Locating the external CLI tools the emulator shells out to.

The API server is normally launched as `.venv/bin/uvicorn ...`, which does NOT
put `.venv/bin` on PATH. Tools installed as Python dependencies (checkov) were
therefore invisible to subprocess.run and every scan silently "passed" without
running. Look next to the running interpreter as well as on PATH.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def resolve_tool(name: str) -> str | None:
    """Return an absolute path to `name`, or None when it is unavailable."""
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(sys.executable).parent / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None
