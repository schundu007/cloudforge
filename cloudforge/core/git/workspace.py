"""
Per-run repository workspace.

A deployed CloudForge has no repository of its own — the container filesystem is
not a useful target. Each run therefore clones the repo it was asked to work on
into a temporary directory, and removes it when the run finishes.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()

# Only these hosts may be cloned. The API is internet-facing, so an unrestricted
# clone target would let a caller point the server at arbitrary internal hosts.
ALLOWED_HOSTS = {"github.com", "www.github.com"}

_REPO_RE = re.compile(
    r"^https://(?P<host>[A-Za-z0-9.-]+)/(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?$"
)


class RepoError(RuntimeError):
    """Raised when a repository cannot be resolved or cloned."""


@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.name}.git"


def parse_repo_url(repo_url: str) -> RepoRef:
    """Parse and validate an HTTPS repository URL."""
    m = _REPO_RE.match((repo_url or "").strip())
    if not m:
        raise RepoError(
            "repo_url must look like https://github.com/<owner>/<repo>"
        )
    host = m.group("host").lower()
    if host not in ALLOWED_HOSTS:
        raise RepoError(f"host '{host}' is not an allowed clone target")
    return RepoRef(host=host, owner=m.group("owner"), name=m.group("name"))


def _redact(text: str, *secrets: str) -> str:
    """Strip credentials from git output before it reaches a log or an API response."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    # git echoes the remote on failure, which carries the basic-auth prefix.
    return re.sub(r"https://[^@/\s]+@", "https://***@", text)


class RepoWorkspace:
    """A shallow clone that exists only for the duration of one run."""

    def __init__(self, repo_url: str, branch: str | None = None, token: str = "") -> None:
        self.ref = parse_repo_url(repo_url)
        self.branch = branch
        self._token = token or ""
        self._tmpdir: str | None = None

    @property
    def path(self) -> Path:
        if not self._tmpdir:
            raise RepoError("workspace is not open")
        return Path(self._tmpdir)

    def _authenticated_url(self) -> str:
        if not self._token:
            return self.ref.clone_url
        # x-access-token is the documented user for GitHub token auth over HTTPS.
        return f"https://x-access-token:{self._token}@{self.ref.host}/{self.ref.owner}/{self.ref.name}.git"

    def open(self) -> "RepoWorkspace":
        self._tmpdir = tempfile.mkdtemp(prefix="cloudforge-repo-")
        cmd = ["git", "clone", "--depth", "1", "--single-branch"]
        if self.branch:
            cmd += ["--branch", self.branch]
        cmd += [self._authenticated_url(), self._tmpdir]

        log.info("workspace.clone", repo=self.ref.full_name, branch=self.branch or "(default)")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            detail = _redact(result.stderr.strip() or result.stdout.strip(), self._token)
            self.close()
            raise RepoError(f"clone failed for {self.ref.full_name}: {detail}")
        return self

    def close(self) -> None:
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def __enter__(self) -> "RepoWorkspace":
        return self.open()

    def __exit__(self, *_exc: object) -> None:
        self.close()
