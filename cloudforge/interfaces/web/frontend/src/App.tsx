import { useState } from 'react';
import { RunList } from './components/RunList';
import { AgentPanel } from './components/AgentPanel';
import { DiffReview } from './components/DiffReview';
import { IntentInput } from './components/IntentInput';
import { ContextPanel } from './components/ContextPanel';
import { useStore } from './store';
import { CloudForgeMark, IconSettings, IconAgent, IconDiff, IconLayers } from './components/icons';

export default function App() {
  const { activeRunId, runs, apiUrl, setApiUrl, repoRoot, setRepoRoot, repoUrl, setRepoUrl } = useStore();
  const activeRun = activeRunId ? runs[activeRunId] : null;
  const [tab, setTab] = useState<'agent' | 'diff' | 'context'>('agent');
  const [showSettings, setShowSettings] = useState(false);

  return (
    <div className="cf-app">
      {/* Sidebar */}
      <aside className="cf-sidebar">
        <div className="cf-logo">
          <span className="cf-logo-icon"><CloudForgeMark size={22} /></span>
          <span className="cf-logo-text">CloudForge</span>
        </div>

        <IntentInput />
        <RunList />

        <button className="cf-settings-btn" onClick={() => setShowSettings(s => !s)}>
          <IconSettings size={13} /> Settings
        </button>

        {showSettings && (
          <div className="cf-settings">
            <label>API URL
              <input value={apiUrl} onChange={e => setApiUrl(e.target.value)} />
            </label>
            <label>Repo URL
              <input
                value={repoUrl}
                placeholder="https://github.com/owner/repo"
                onChange={e => setRepoUrl(e.target.value)}
              />
            </label>
            <label>Repo Root <span className="cf-hint">(local API only)</span>
              <input value={repoRoot} onChange={e => setRepoRoot(e.target.value)} />
            </label>
          </div>
        )}
      </aside>

      {/* Main panel */}
      <main className="cf-main">
        {activeRun ? (
          <>
            <div className="cf-run-header">
              <div className="cf-run-intent">{activeRun.intent}</div>
              <div className="cf-run-meta">
                <StatusBadge status={activeRun.status} />
                {activeRun.fix_iterations > 0 && (
                  <span className="cf-badge cf-badge-warn">
                    {activeRun.fix_iterations} fix{activeRun.fix_iterations > 1 ? 'es' : ''}
                  </span>
                )}
                {activeRun.completed_at && (
                  <span className="cf-time">
                    {((activeRun.completed_at - activeRun.started_at) / 1000).toFixed(1)}s
                  </span>
                )}
              </div>
            </div>

            <div className="cf-tabs">
              {(['agent', 'diff', 'context'] as const).map(t => (
                <button
                  key={t}
                  className={`cf-tab ${tab === t ? 'cf-tab--active' : ''}`}
                  onClick={() => setTab(t)}
                >
                  {t === 'agent' && <IconAgent size={13} />}
                  {t === 'diff' && <IconDiff size={13} />}
                  {t === 'context' && <IconLayers size={13} />}
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                  {t === 'diff' && activeRun.files.length > 0 && (
                    <span className="cf-tab-count">{activeRun.files.length}</span>
                  )}
                </button>
              ))}
            </div>

            <div className="cf-panel-body">
              {tab === 'agent' && <AgentPanel run={activeRun} />}
              {tab === 'diff' && <DiffReview run={activeRun} />}
              {tab === 'context' && <ContextPanel run={activeRun} />}
            </div>
          </>
        ) : (
          <EmptyState />
        )}
      </main>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    pending: { label: 'Pending', cls: 'cf-badge-neutral' },
    running: { label: 'Running', cls: 'cf-badge-info cf-badge-pulse' },
    complete: { label: 'Complete', cls: 'cf-badge-success' },
    error: { label: 'Error', cls: 'cf-badge-danger' },
    escalated: { label: 'Escalated', cls: 'cf-badge-danger' },
    pr_opened: { label: 'PR Opened', cls: 'cf-badge-success' },
  };
  const { label, cls } = map[status] ?? { label: status, cls: 'cf-badge-neutral' };
  return <span className={`cf-badge ${cls}`}>{label}</span>;
}

function EmptyState() {
  return (
    <div className="cf-empty">
      <div className="cf-empty-icon"><CloudForgeMark size={56} /></div>
      <h2>CloudForge</h2>
      <p>Describe what you want to build or fix — pipelines, IaC, security policies, or observability configs.</p>
      <div className="cf-examples">
        {[
          'Add OIDC auth to all deploy workflows',
          'Add private subnet with NAT gateway to networking module',
          'Scan and fix all HIGH checkov findings in modules/',
          'Add OTel + Prometheus + Grafana for the payments service',
          'Generate least-privilege IAM role for the Lambda function',
        ].map(ex => (
          <ExampleChip key={ex} text={ex} />
        ))}
      </div>
    </div>
  );
}

function ExampleChip({ text }: { text: string }) {
  const { startRun } = useStore();
  return (
    <button className="cf-example" onClick={() => startRun(text)}>
      {text}
    </button>
  );
}
