"""
Robust parsing of LLM responses that are expected to contain JSON.

Two quirks show up constantly in practice and both used to corrupt generated
files:

1. The model wraps its JSON in a markdown fence (```json ... ```).
2. The model emits literal newlines inside JSON string values, which strict
   json.loads rejects with "Invalid control character".

Either one made the writers fall back to treating the *entire* raw response as
file content, so a fence line like ```json ended up as line 1 of main.tf and
every terraform plan failed. Both are handled here.
"""
from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_+-]*[ \t]*\r?\n(.*?)\r?\n?[ \t]*```$", re.DOTALL)


_EMBEDDED_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*[ \t]*\r?\n(.*?)\r?\n?[ \t]*```", re.DOTALL)


def strip_code_fence(raw: str) -> str:
    """Return the body of a fenced block, or the input unchanged if unfenced.

    Models often precede the fence with prose ("I'll generate ..."), so an
    embedded block counts too, not just a string that is entirely a fence.
    """
    text = raw.strip()
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    m = _EMBEDDED_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text


def extract_json(raw: str) -> dict | None:
    """Best-effort extraction of a JSON object from a model response.

    Tolerates markdown fences and unescaped control characters inside strings
    (strict=False). Returns None when nothing parses as a JSON object.
    """
    text = strip_code_fence(raw)
    candidates = []
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group())
    candidates.append(text)

    for candidate in candidates:
        try:
            data = json.loads(candidate, strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


# A model that writes HCL/YAML into a JSON string very often forgets to escape
# the inner double quotes, producing JSON no parser can accept. Rather than
# dumping the whole wrapper into a .tf file, pull the entries out by shape.
_OTHER_KEYS = r'(?:\s*,\s*"[A-Za-z_][A-Za-z0-9_]*"\s*:\s*"[^"]*")*'

# path ... content  (any number of simple string keys in between, e.g. "type")
_FILE_ENTRY_RE = re.compile(
    r'"path"\s*:\s*"([^"]+)"' + _OTHER_KEYS + r'\s*,\s*"content"\s*:\s*"(.*?)"\s*\}\s*(?=[,\]])',
    re.DOTALL,
)

# content ... path  (some models emit the reverse order)
_FILE_ENTRY_REV_RE = re.compile(
    r'"content"\s*:\s*"(.*?)"' + _OTHER_KEYS + r'\s*,\s*"path"\s*:\s*"([^"]+)"',
    re.DOTALL,
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), value)


def salvage_files(raw: str) -> list[dict]:
    """Recover [{path, content}] from a malformed JSON files wrapper."""
    text = strip_code_fence(raw)
    found = [
        {"path": path, "content": _unescape(content)}
        for path, content in _FILE_ENTRY_RE.findall(text)
    ]
    if found:
        return found
    return [
        {"path": path, "content": _unescape(content)}
        for content, path in _FILE_ENTRY_REV_RE.findall(text)
    ]


def parse_files(raw: str, default_path: str, extra: dict | None = None) -> list[dict]:
    """Parse a {"files": [{"path", "content"}]} response.

    Falls back to treating the response as the body of a single file at
    default_path — with any markdown fence stripped, so fence markers never
    reach disk.
    """
    data = extract_json(raw)
    if data:
        files = data.get("files")
        if isinstance(files, list):
            valid = [
                f for f in files
                if isinstance(f, dict) and f.get("path") and f.get("content") is not None
            ]
            if valid:
                return valid

    salvaged = salvage_files(raw)
    if salvaged:
        if extra:
            for f in salvaged:
                f.update(extra)
        return salvaged

    entry: dict = {"path": default_path, "content": strip_code_fence(raw)}
    if extra:
        entry.update(extra)
    return [entry]

def extract_json_list(raw: str) -> list | None:
    """Best-effort extraction of a JSON array from a model response.

    Accepts a bare array, a fenced array, or an object wrapping the array
    under a single key (e.g. {"tasks": [...]}).
    """
    text = strip_code_fence(raw)
    candidates = []
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        candidates.append(m.group())
    candidates.append(text)

    for candidate in candidates:
        try:
            data = json.loads(candidate, strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    return value
    return None
