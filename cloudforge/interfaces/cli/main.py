"""
CloudForge CLI
Terminal interface with Rich streaming output and interactive diff review.
Usage: cloudforge run "add OIDC auth to deploy pipeline"
       cloudforge scan ./modules/networking
       cloudforge hook pre-commit
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.syntax import Syntax
from rich.table import Table
from rich import print as rprint

app = typer.Typer(
    name="cloudforge",
    help="Cloud infrastructure coding agent — pipelines, IaC, security, observability",
    add_completion=False,
)
console = Console()

EVENT_ICONS = {
    "context_loaded": "📂",
    "classified": "🎯",
    "agent_start": "🤖",
    "agent_done": "✅",
    "emulator_start": "🏃",
    "emulator_pass": "✅",
    "emulator_fail": "❌",
    "fix_start": "🔧",
    "diff_ready": "📋",
    "escalate": "🚨",
}


@app.command("run")
def run_cmd(
    intent: str = typer.Argument(..., help="What to build or fix"),
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Repo root path"),
    auto_accept: bool = typer.Option(False, "--yes", "-y", help="Auto-accept diff without review"),
    pr: bool = typer.Option(False, "--pr", help="Open GitHub PR after acceptance"),
):
    """Run the CloudForge agent on your infrastructure repo."""
    _check_env()
    asyncio.run(_run_async(intent, repo, auto_accept, pr))


@app.command("scan")
def scan_cmd(
    path: Path = typer.Argument(Path("."), help="Path to scan"),
    fix: bool = typer.Option(False, "--fix", help="Auto-fix security findings"),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table|json|sarif"),
):
    """Scan IaC files with checkov + tfsec and optionally auto-fix."""
    console.rule("[bold red]CloudForge Security Scan")

    intent = f"Scan and {'fix' if fix else 'report'} all security issues in {path}"
    asyncio.run(_run_async(intent, path if path.is_dir() else path.parent, auto_accept=fix, pr=False))


@app.command("hook")
def hook_cmd(
    hook_type: str = typer.Argument(..., help="Hook type: pre-commit|pr|cost|drift"),
    payload_file: Path | None = typer.Option(None, "--payload", help="JSON payload file"),
):
    """Trigger a CloudForge hook manually."""
    import json
    payload = {}
    if payload_file and payload_file.exists():
        payload = json.loads(payload_file.read_text())

    asyncio.run(_dispatch_hook(hook_type, payload))


@app.command("context")
def context_cmd(
    repo: Path = typer.Option(Path("."), "--repo", "-r"),
):
    """Show the current infrastructure context CloudForge sees."""
    from cloudforge.core.context.store import ContextStore
    ctx_store = ContextStore(repo)
    ctx = ctx_store.refresh()

    table = Table(title="CloudForge Context", show_header=True)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Repo root", str(ctx.root))
    table.add_row("TF modules", "\n".join(ctx.tf_modules) or "none")
    table.add_row("GHA workflows", "\n".join(ctx.gha_workflows) or "none")
    table.add_row("Files scanned", str(len(ctx.tree)))
    table.add_row("TF resources in state",
                  str(len(ctx.tf_state.get("values", {}).get("root_module", {}).get("resources", []))))
    console.print(table)


# ------------------------------------------------------------------
# Async helpers
# ------------------------------------------------------------------
async def _run_async(intent: str, repo: Path, auto_accept: bool, pr: bool) -> None:
    from cloudforge.core.agents.orchestrator import Orchestrator

    console.rule(f"[bold cyan]CloudForge[/bold cyan] · [dim]{intent}[/dim]")

    orch = Orchestrator(
        repo_root=repo.resolve(),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
    )

    files: list[dict] = []
    diff: str = ""

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                  console=console, transient=True) as progress:
        task = progress.add_task("Starting agent...", total=None)

        async for event in orch.run(intent):
            icon = EVENT_ICONS.get(event.get("event", ""), "·")
            ev = event.get("event", "")

            if ev == "context_loaded":
                progress.update(task, description=f"📂 Context: {len(event.get('tf_modules', []))} TF modules, {len(event.get('workflows', []))} workflows")

            elif ev == "classified":
                progress.update(task, description=f"🎯 Task type: {event['task_type']}")

            elif ev == "agent_start":
                progress.update(task, description=f"🤖 {event['agent']} agent → {event['intent'][:60]}...")

            elif ev == "agent_done":
                rprint(f"  {icon} [green]{event['agent']} agent[/green] — generated: {', '.join(event.get('files', []))}")

            elif ev == "emulator_start":
                progress.update(task, description=f"🏃 Emulator run #{event['iteration'] + 1}")

            elif ev == "emulator_pass":
                rprint(f"  ✅ [green]Emulator passed[/green] · {event.get('summary', '')}")

            elif ev == "emulator_fail":
                errors = event.get("errors", [])
                rprint(f"  ❌ [red]Emulator failed[/red] · {len(errors)} error(s)")
                for err in errors[:3]:
                    rprint(f"     [dim]{err.get('type', '?')}[/dim]: {err.get('message', '')[:120]}")

            elif ev == "fix_start":
                rprint(f"  🔧 [yellow]Auto-fix[/yellow] · {event.get('patches', 0)} patch(es)")

            elif ev == "escalate":
                rprint(Panel(
                    f"[red]{event.get('message')}[/red]\n\nErrors:\n" +
                    "\n".join(str(e) for e in event.get("errors", [])[:5]),
                    title="🚨 Escalation Required", border_style="red",
                ))

            elif ev == "diff_ready":
                files = event.get("files", [])
                diff = event.get("diff", "")
                progress.update(task, description="📋 Diff ready for review")

    if not files:
        rprint("[yellow]No files generated.[/yellow]")
        return

    # Show diff
    _show_diff_summary(files)

    if auto_accept or typer.confirm("\nAccept this diff and open a PR?"):
        _write_files_locally(files, repo)
        rprint("\n[green]Files written to repo.[/green]")
        if pr:
            rprint("[dim]→ Open a PR with: gh pr create --fill[/dim]")
    else:
        rprint("[dim]Diff rejected. Re-run with updated intent.[/dim]")


async def _dispatch_hook(hook_type: str, payload: dict) -> None:
    from cloudforge.core.hooks.dispatcher import HookDispatcher
    console.print(f"[cyan]Dispatching hook:[/cyan] {hook_type}")
    dispatcher = HookDispatcher()
    run_id = await dispatcher.dispatch(hook_type, payload)
    console.print(f"[green]Hook dispatched → run_id: {run_id}[/green]")


def _show_diff_summary(files: list[dict]) -> None:
    console.rule("Generated Diff")
    for f in files:
        icon = {"pipeline": "⚙️", "iac": "🏗️", "security": "🔒", "observability": "📡"}.get(f.get("agent", ""), "📄")
        checks = []
        if f.get("checkov", {}).get("passed") is True:
            checks.append("[green]checkov ✓[/green]")
        elif f.get("checkov", {}).get("passed") is False:
            checks.append("[red]checkov ✗[/red]")
        if f.get("tfsec", {}).get("passed") is True:
            checks.append("[green]tfsec ✓[/green]")
        if f.get("actionlint", {}).get("passed") is True:
            checks.append("[green]actionlint ✓[/green]")

        checks_str = " · ".join(checks) if checks else ""
        rprint(f"  {icon} [bold]{f['path']}[/bold] {checks_str}")

        if f.get("diff"):
            syntax = Syntax(f["diff"][:800], "diff", theme="monokai", line_numbers=False)
            console.print(syntax)


def _write_files_locally(files: list[dict], repo: Path) -> None:
    for f in files:
        full_path = repo / f["path"]
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(f["content"])
        rprint(f"  [dim]wrote → {f['path']}[/dim]")


def _check_env() -> None:
    missing = [k for k in ["ANTHROPIC_API_KEY"] if not os.getenv(k)]
    if missing:
        rprint(f"[red]Missing env vars: {', '.join(missing)}[/red]")
        rprint("[dim]Set ANTHROPIC_API_KEY to continue.[/dim]")
        raise typer.Exit(1)


from cloudforge.interfaces.cli.hooks import app as hooks_app
app.add_typer(hooks_app, name="hook", help="Manage CloudForge lifecycle hooks")

if __name__ == "__main__":
    app()
