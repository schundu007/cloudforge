import { useState, useRef } from 'react';
import { useStore, type Run } from '../store';
import { IconBolt, IconStatusDot, IconCube, IconBranch } from './icons';
import { formatDistanceToNow } from 'date-fns';

// ── IntentInput ────────────────────────────────────────────────────
const SUGGESTIONS = [
  'Add OIDC auth to all deploy workflows',
  'Add private subnet with NAT gateway',
  'Fix all HIGH checkov findings in modules/',
  'Add OTel + Prometheus for payments service',
  'Generate least-privilege IAM for Lambda',
  'Add GCP workload identity federation',
  'Drift detection for networking module',
  'Add Azure OIDC to deploy pipeline',
];

export function IntentInput() {
  const { startRun } = useStore();
  const [value, setValue] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const v = e.target.value;
    setValue(v);
    if (v.length > 2) {
      const filtered = SUGGESTIONS.filter(s => s.toLowerCase().includes(v.toLowerCase()));
      setSuggestions(filtered.slice(0, 4));
    } else {
      setSuggestions([]);
    }
  }

  async function submit(intent: string) {
    if (!intent.trim() || loading) return;
    setLoading(true);
    setSuggestions([]);
    setValue('');
    try {
      await startRun(intent.trim());
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit(value);
    }
  }

  return (
    <div className="cf-intent-wrap">
      <div className="cf-intent-box">
        <textarea
          ref={inputRef}
          className="cf-intent-input"
          placeholder="What should CloudForge build or fix?&#10;⏎ to run   Shift+⏎ newline"
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          rows={3}
          disabled={loading}
        />
        <button
          className="cf-intent-btn"
          onClick={() => submit(value)}
          disabled={!value.trim() || loading}
        >
          {loading ? <span className="cf-spinner cf-spinner-sm" /> : <><IconBolt size={13} /> Run</>}
        </button>
      </div>

      {suggestions.length > 0 && (
        <div className="cf-suggestions">
          {suggestions.map(s => (
            <button key={s} className="cf-suggestion" onClick={() => submit(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── RunList ────────────────────────────────────────────────────────
export function RunList() {
  const { runs, activeRunId, setActiveRun } = useStore();
  const runList = Object.values(runs).sort((a, b) => b.started_at - a.started_at);

  if (!runList.length) return null;

  return (
    <div className="cf-run-list">
      <div className="cf-run-list-header">Recent Runs</div>
      {runList.map(run => (
        <RunItem key={run.id} run={run} active={run.id === activeRunId} onClick={() => setActiveRun(run.id)} />
      ))}
    </div>
  );
}

function RunItem({ run, active, onClick }: { run: Run; active: boolean; onClick: () => void }) {
  const statusDot: Record<string, string> = {
    running: 'running', pending: 'pending', complete: 'complete',
    error: 'error', escalated: 'escalated', pr_opened: 'pr_opened',
  };
  const dot = statusDot[run.status] ?? 'pending';

  return (
    <button className={`cf-run-item ${active ? 'cf-run-item--active' : ''}`} onClick={onClick}>
      <span className="cf-run-dot"><IconStatusDot status={dot} size={9} /></span>
      <span className="cf-run-item-text">{run.intent.slice(0, 52)}</span>
      <span className="cf-run-age">
        {formatDistanceToNow(run.started_at, { addSuffix: false }).replace('about ', '')}
      </span>
    </button>
  );
}

// ── ContextPanel ───────────────────────────────────────────────────
export function ContextPanel({ run }: { run: Run }) {
  const contextEvent = run.events.find(e => e.event === 'context_loaded');

  return (
    <div className="cf-context-panel">
      {contextEvent ? (
        <>
          <Section title="TF Modules">
            {(contextEvent.tf_modules ?? []).map(m => (
              <div key={m} className="cf-ctx-item"><span className="cf-ctx-icon"><IconCube size={12} /></span>{m}</div>
            ))}
            {!contextEvent.tf_modules?.length && <div className="cf-ctx-empty">No modules found</div>}
          </Section>

          <Section title="GitHub Actions Workflows">
            {(contextEvent.workflows ?? []).map(w => (
              <div key={w} className="cf-ctx-item"><span className="cf-ctx-icon"><IconBranch size={12} /></span>{w}</div>
            ))}
            {!contextEvent.workflows?.length && <div className="cf-ctx-empty">No workflows found</div>}
          </Section>

          <Section title="Run Timeline">
            <div className="cf-timeline">
              {run.events.map((ev, i) => (
                <div key={i} className="cf-timeline-row">
                  <span className="cf-timeline-ts">
                    {ev.timestamp
                      ? new Date(ev.timestamp - run.started_at).toISOString().substr(11, 8)
                      : ''}
                  </span>
                  <span className="cf-timeline-event">{ev.event}</span>
                  {ev.agent && <span className="cf-timeline-agent">[{ev.agent}]</span>}
                </div>
              ))}
            </div>
          </Section>
        </>
      ) : (
        <div className="cf-ctx-empty">Context not yet loaded</div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="cf-ctx-section">
      <div className="cf-ctx-section-title">{title}</div>
      {children}
    </div>
  );
}
