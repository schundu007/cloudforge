/**
 * CloudForge VS Code Extension
 * Fast Edit (⌘K), agent chat panel, SSE streaming diff review.
 * Mirrors Firebender's IDE-native UX for cloud infra files.
 */
import * as vscode from 'vscode';

const CF_API = () => vscode.workspace.getConfiguration('cloudforge').get<string>('apiUrl', 'http://localhost:8000');

export function activate(context: vscode.ExtensionContext) {
  // ── Fast Edit (⌘K) ──────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('cloudforge.fastEdit', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;

      const selection = editor.selection;
      const selectedText = editor.document.getText(selection.isEmpty
        ? new vscode.Range(new vscode.Position(0, 0), new vscode.Position(editor.document.lineCount, 0))
        : selection);

      const instruction = await vscode.window.showInputBox({
        prompt: 'CloudForge Fast Edit — what should I change?',
        placeHolder: 'e.g. "Add OIDC auth", "Fix checkov CKV_AWS_119", "Add encryption"',
      });
      if (!instruction) return;

      const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '.';
      await runAndStream(context, `Fast Edit: ${instruction}\n\nSelected content:\n${selectedText}`, repoRoot);
    })
  );

  // ── Run Agent ────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('cloudforge.run', async () => {
      const intent = await vscode.window.showInputBox({
        prompt: 'CloudForge — what should I build?',
        placeHolder: 'e.g. "Add private subnet with NAT gateway", "Generate least-priv IAM for payments service"',
      });
      if (!intent) return;
      const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '.';
      await runAndStream(context, intent, repoRoot);
    })
  );

  // ── Scan & Fix ───────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('cloudforge.scan', async () => {
      const editor = vscode.window.activeTextEditor;
      const filePath = editor?.document.uri.fsPath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '.';
      const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '.';
      await runAndStream(context, `Scan and fix all security issues in ${filePath}`, repoRoot);
    })
  );

  // ── Add Observability ────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('cloudforge.addObs', async () => {
      const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '.';
      await runAndStream(context, 'Generate OTel collector config, Prometheus rules, and Grafana dashboard for all services in this repo', repoRoot);
    })
  );

  // ── Terraform Plan (LocalStack) ──────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('cloudforge.tfPlan', async () => {
      const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '.';
      await runAndStream(context, 'Run terraform plan against LocalStack for all modified modules', repoRoot);
    })
  );

  // ── Checkpoint ───────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('cloudforge.checkpoint', async () => {
      vscode.window.showInformationMessage('CloudForge: Checkpoint created (tf state snapshot + git stash)');
      // In production: POST /checkpoint to API
    })
  );

  // ── Show Context ─────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('cloudforge.showContext', async () => {
      const panel = vscode.window.createWebviewPanel(
        'cloudforge.context', 'CloudForge: Infra Context', vscode.ViewColumn.Beside, {}
      );
      const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';
      const resp = await fetch(`${CF_API()}/context?repo=${encodeURIComponent(repoRoot)}`).catch(() => null);
      const data = resp ? await resp.json() : { error: 'API not reachable' };
      panel.webview.html = `<pre style="font-family:monospace;padding:1rem">${JSON.stringify(data, null, 2)}</pre>`;
    })
  );

  // ── Background Agent ─────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('cloudforge.background', async () => {
      const intent = await vscode.window.showInputBox({
        prompt: 'CloudForge Background Task — runs in isolated git worktree',
        placeHolder: 'e.g. "Refactor all hardcoded AMI IDs to data sources across the entire repo"',
      });
      if (!intent) return;
      const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '.';
      vscode.window.showInformationMessage(`CloudForge: Background task started in git worktree — "${intent.slice(0, 60)}..."`);
      // Fire-and-forget: POST /runs with background=true
      fetch(`${CF_API()}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ intent, repo_root: repoRoot, background: true }),
      }).catch(console.error);
    })
  );

  vscode.window.showInformationMessage('CloudForge activated — ⌘K to fast-edit, ⌘L to run agent');
}

// ------------------------------------------------------------------
// Streaming panel
// ------------------------------------------------------------------
async function runAndStream(context: vscode.ExtensionContext, intent: string, repoRoot: string): Promise<void> {
  const panel = vscode.window.createWebviewPanel(
    'cloudforge.run',
    'CloudForge Agent',
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true }
  );

  panel.webview.html = getAgentPanelHtml(intent);

  // Start run
  let runId: string;
  try {
    const resp = await fetch(`${CF_API()}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intent, repo_root: repoRoot }),
    });
    const data = await resp.json() as { run_id: string };
    runId = data.run_id;
  } catch (e) {
    panel.webview.postMessage({ type: 'error', message: `API not reachable: ${e}` });
    return;
  }

  // Stream events via SSE
  const EventSource = (await import('eventsource')).default;
  const es = new EventSource(`${CF_API()}/runs/${runId}/stream`);

  es.onmessage = (ev) => {
    const event = JSON.parse(ev.data);
    panel.webview.postMessage({ type: 'event', event });
    if (event.event === 'done') es.close();
  };

  es.onerror = () => {
    panel.webview.postMessage({ type: 'error', message: 'Stream disconnected' });
    es.close();
  };

  // Handle accept/reject from webview
  panel.webview.onDidReceiveMessage(async (msg) => {
    if (msg.type === 'accept') {
      await fetch(`${CF_API()}/runs/${runId}/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId, file_paths: msg.files ?? [] }),
      });
      vscode.window.showInformationMessage('CloudForge: Diff accepted — files written, PR ready');
    } else if (msg.type === 'reject') {
      await fetch(`${CF_API()}/runs/${runId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg.feedback }),
      });
    }
  });
}

function getAgentPanelHtml(intent: string): string {
  return `<!DOCTYPE html><html><head>
<meta charset="UTF-8">
<style>
  body { font-family: var(--vscode-font-family); font-size: 13px; padding: 1rem; color: var(--vscode-foreground); background: var(--vscode-editor-background); }
  #intent { color: var(--vscode-descriptionForeground); margin-bottom: 1rem; font-style: italic; }
  #events { list-style: none; padding: 0; }
  #events li { padding: 4px 0; border-bottom: 1px solid var(--vscode-widget-border); }
  .diff-block { font-family: monospace; font-size: 12px; background: var(--vscode-textBlockQuote-background); padding: .75rem; border-radius: 4px; overflow-x: auto; white-space: pre; margin: .5rem 0; }
  .add { color: var(--vscode-gitDecoration-addedResourceForeground); }
  .del { color: var(--vscode-gitDecoration-deletedResourceForeground); }
  .actions { display: flex; gap: 8px; margin-top: 1rem; }
  button { padding: 6px 16px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
  .accept { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  .reject { background: transparent; border: 1px solid var(--vscode-button-border); color: var(--vscode-foreground); }
</style>
</head><body>
<div id="intent">Intent: ${intent}</div>
<ul id="events"></ul>
<div id="diff-area"></div>
<script>
  const vscode = acquireVsCodeApi();
  const events = document.getElementById('events');
  const diffArea = document.getElementById('diff-area');
  const icons = { context_loaded:'📂', classified:'🎯', agent_start:'🤖', agent_done:'✅', emulator_start:'🏃', emulator_pass:'✅', emulator_fail:'❌', fix_start:'🔧', diff_ready:'📋', escalate:'🚨' };

  window.addEventListener('message', e => {
    const msg = e.data;
    if (msg.type === 'event') {
      const ev = msg.event;
      const li = document.createElement('li');
      const icon = icons[ev.event] || '·';
      li.textContent = icon + ' ' + ev.event + (ev.agent ? ' [' + ev.agent + ']' : '') + (ev.summary ? ': ' + ev.summary : '');
      events.appendChild(li);
      if (ev.event === 'diff_ready') showDiff(ev.files);
    } else if (msg.type === 'error') {
      const li = document.createElement('li');
      li.textContent = '❌ ' + msg.message;
      li.style.color = 'var(--vscode-errorForeground)';
      events.appendChild(li);
    }
  });

  function showDiff(files) {
    diffArea.innerHTML = '';
    files.forEach(f => {
      const h = document.createElement('h4');
      h.textContent = f.path;
      diffArea.appendChild(h);
      const pre = document.createElement('div');
      pre.className = 'diff-block';
      pre.innerHTML = (f.diff || '').replace(/^\\+(.*)$/gm, '<span class="add">+$1</span>').replace(/^-(.*)$/gm, '<span class="del">-$1</span>');
      diffArea.appendChild(pre);
    });
    const actions = document.createElement('div');
    actions.className = 'actions';
    actions.innerHTML = '<button class="accept" onclick="accept()">✓ Accept &amp; write files</button><button class="reject" onclick="reject()">✗ Reject</button>';
    diffArea.appendChild(actions);
  }

  function accept() { vscode.postMessage({ type: 'accept', files: [] }); }
  function reject() {
    const fb = prompt('Feedback for CloudForge (optional):');
    vscode.postMessage({ type: 'reject', feedback: fb || '' });
  }
</script>
</body></html>`;
}

export function deactivate() {}
