"""
CloudForge hook install/uninstall commands.
Wires CloudForge into the repo and CI/CD system:
  - pre-commit: .git/hooks/pre-commit
  - pre-commit framework: .pre-commit-config.yaml
  - GitHub Actions: .github/workflows/cloudforge-hooks.yml
  - Drift cron: added to GHA schedule workflow
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table
from rich.console import Console

app = typer.Typer(help="Manage CloudForge lifecycle hooks")
console = Console()


@app.command("install")
def install(
    repo: Path = typer.Option(Path("."), "--repo", "-r"),
    hooks: list[str] = typer.Option(
        ["pre-commit", "pr", "drift", "deploy"],
        "--hook", "-H",
        help="Hooks to install: pre-commit, pr, drift, deploy, cost, log-error",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be installed"),
):
    """Install CloudForge hooks into a repository."""
    console.rule("[bold cyan]CloudForge Hook Installer")

    git_root = _find_git_root(repo)
    if not git_root:
        rprint("[red]No git repository found.[/red]")
        raise typer.Exit(1)

    results = []

    if "pre-commit" in hooks:
        result = _install_git_pre_commit(git_root, dry_run)
        results.append(("pre-commit git hook", result))

        result2 = _install_pre_commit_framework(git_root, dry_run)
        results.append(("pre-commit-hooks.yaml", result2))

    if any(h in hooks for h in ["pr", "drift", "deploy", "cost", "log-error"]):
        result = _install_gha_hooks_workflow(git_root, hooks, dry_run)
        results.append((".github/workflows/cloudforge-hooks.yml", result))

    if "drift" in hooks:
        result = _install_drift_cron(git_root, dry_run)
        results.append(("drift cron (GHA schedule)", result))

    # Summary table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Hook / File")
    table.add_column("Status")
    table.add_column("Path")

    for name, (status, path, msg) in results:
        icon = "✅" if status == "ok" else ("⏭" if status == "skip" else "❌")
        table.add_row(name, f"{icon} {msg}", str(path) if path else "—")

    console.print(table)

    if not dry_run:
        rprint("\n[green]CloudForge hooks installed.[/green]")
        rprint("[dim]Run 'cloudforge hook status' to verify.[/dim]")


@app.command("uninstall")
def uninstall(
    repo: Path = typer.Option(Path("."), "--repo", "-r"),
):
    """Remove all CloudForge hooks from a repository."""
    git_root = _find_git_root(repo)
    if not git_root:
        rprint("[red]No git repository found.[/red]")
        raise typer.Exit(1)

    removed = []

    # Remove git hook
    hook_path = git_root / ".git" / "hooks" / "pre-commit"
    if hook_path.exists() and "cloudforge" in hook_path.read_text():
        hook_path.unlink()
        removed.append(str(hook_path))

    # Remove GHA workflow
    wf_path = git_root / ".github" / "workflows" / "cloudforge-hooks.yml"
    if wf_path.exists():
        wf_path.unlink()
        removed.append(str(wf_path))

    for path in removed:
        rprint(f"  [dim]removed:[/dim] {path}")
    if not removed:
        rprint("[dim]Nothing to remove.[/dim]")


@app.command("status")
def status(
    repo: Path = typer.Option(Path("."), "--repo", "-r"),
):
    """Show which CloudForge hooks are active."""
    git_root = _find_git_root(repo)

    checks = [
        ("pre-commit git hook", git_root / ".git" / "hooks" / "pre-commit" if git_root else None),
        ("pre-commit-config.yaml", repo / ".pre-commit-config.yaml"),
        ("cloudforge.yaml", repo / "cloudforge.yaml"),
        ("cloudforge-hooks.yml (GHA)", repo / ".github" / "workflows" / "cloudforge-hooks.yml"),
        ("drift cron (GHA)", repo / ".github" / "workflows" / "cloudforge-drift.yml"),
    ]

    table = Table(show_header=True)
    table.add_column("Hook")
    table.add_column("Status")
    for name, path in checks:
        active = path and path.exists()
        icon = "✅ Active" if active else "⭕ Not installed"
        table.add_row(name, icon)
    console.print(table)


# ------------------------------------------------------------------
# Internal installers
# ------------------------------------------------------------------
def _install_git_pre_commit(git_root: Path, dry_run: bool) -> tuple:
    hook_path = git_root / ".git" / "hooks" / "pre-commit"
    scripts_src = Path(__file__).parent.parent.parent.parent / "scripts" / "pre-commit"

    if not scripts_src.exists():
        # Inline the hook script if scripts/ dir not found
        hook_content = _pre_commit_hook_script()
    else:
        hook_content = scripts_src.read_text()

    if hook_path.exists() and "cloudforge" in hook_path.read_text():
        return ("skip", hook_path, "already installed")

    if dry_run:
        return ("ok", hook_path, "would install")

    hook_path.write_text(hook_content)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return ("ok", hook_path, "installed")


def _install_pre_commit_framework(git_root: Path, dry_run: bool) -> tuple:
    config_path = git_root / ".pre-commit-config.yaml"
    hook_entry = """  - repo: local
    hooks:
      - id: cloudforge-security-gate
        name: CloudForge security gate
        entry: cloudforge hook run pre-commit
        language: python
        files: \\.tf$
        pass_filenames: false
"""
    if config_path.exists() and "cloudforge" in config_path.read_text():
        return ("skip", config_path, "already in .pre-commit-config.yaml")

    if dry_run:
        return ("ok", config_path, "would add cloudforge entry")

    existing = config_path.read_text() if config_path.exists() else "repos:\n"
    if "repos:" not in existing:
        existing = "repos:\n" + existing
    config_path.write_text(existing + hook_entry)
    return ("ok", config_path, "entry added")


def _install_gha_hooks_workflow(git_root: Path, hooks: list[str], dry_run: bool) -> tuple:
    wf_dir = git_root / ".github" / "workflows"
    wf_path = wf_dir / "cloudforge-hooks.yml"

    if wf_path.exists():
        return ("skip", wf_path, "already exists")

    wf_dir.mkdir(parents=True, exist_ok=True)
    content = _hooks_workflow_yaml(hooks)

    if dry_run:
        return ("ok", wf_path, "would create")

    wf_path.write_text(content)
    return ("ok", wf_path, "created")


def _install_drift_cron(git_root: Path, dry_run: bool) -> tuple:
    wf_dir = git_root / ".github" / "workflows"
    wf_path = wf_dir / "cloudforge-drift.yml"

    if wf_path.exists():
        return ("skip", wf_path, "already exists")

    if dry_run:
        return ("ok", wf_path, "would create")

    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_path.write_text(_drift_workflow_yaml())
    return ("ok", wf_path, "created")


def _find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def _hooks_workflow_yaml(hooks: list[str]) -> str:
    triggers = []
    if "pr" in hooks:
        triggers.append("  pull_request:\n    types: [opened, synchronize, labeled]")
    if "deploy" in hooks:
        triggers.append("  push:\n    branches: [main]")

    return f"""name: CloudForge Hooks

on:
{chr(10).join(triggers)}
  workflow_dispatch:

permissions:
  id-token: write
  contents: read
  pull-requests: write

jobs:
  cloudforge:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install CloudForge
        run: pip install cloudforge

      - name: Run CloudForge hook
        env:
          ANTHROPIC_API_KEY: ${{{{ secrets.ANTHROPIC_API_KEY }}}}
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: |
          if [ "${{{{ github.event_name }}}}" = "pull_request" ]; then
            cloudforge hook pr-opened --payload '${{{{ toJson(github.event) }}}}'
          elif [ "${{{{ github.event_name }}}}" = "push" ]; then
            cloudforge hook deploy:prod --payload '${{{{ toJson(github.event) }}}}'
          fi
"""


def _drift_workflow_yaml() -> str:
    return """name: CloudForge Drift Detection

on:
  schedule:
    - cron: "0 */6 * * *"   # every 6 hours
  workflow_dispatch:

permissions:
  id-token: write
  contents: read
  pull-requests: write

jobs:
  drift-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS via OIDC
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_READONLY_ROLE_ARN }}
          aws-region: us-east-1

      - name: Install CloudForge
        run: pip install cloudforge

      - name: Check for drift
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: cloudforge hook drift --payload '{}'
"""


def _pre_commit_hook_script() -> str:
    return '''#!/usr/bin/env python3
"""CloudForge pre-commit security gate."""
import subprocess, sys

result = subprocess.run(["cloudforge", "hook", "pre-commit",
                         "--payload", "{}"], capture_output=False)
sys.exit(result.returncode)
'''
