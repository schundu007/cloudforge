import { useState } from 'react';
import { useStore, type Run, type GeneratedFile } from '../store';
import { AgentIcon, IconCheck, IconCheckMark, IconXMark, IconArrowUpRight } from './icons';

interface Props {
  run: Run;
}

export function DiffReview({ run }: Props) {
  const { acceptDiff, rejectDiff } = useStore();
  const [selected, setSelected] = useState<Set<string>>(new Set(run.files.map(f => f.path)));
  const [rejectFeedback, setRejectFeedback] = useState('');
  const [showReject, setShowReject] = useState(false);
  const [activeFile, setActiveFile] = useState<string | null>(run.files[0]?.path ?? null);

  if (!run.files.length) {
    return (
      <div className="cf-diff-empty">
        {run.status === 'running' ? (
          <><span className="cf-spinner" /> Generating diff…</>
        ) : (
          'No files generated yet.'
        )}
      </div>
    );
  }

  const activeFileData = run.files.find(f => f.path === activeFile);
  const allSelected = selected.size === run.files.length;

  function toggleFile(path: string) {
    setSelected(s => {
      const n = new Set(s);
      n.has(path) ? n.delete(path) : n.add(path);
      return n;
    });
  }

  async function handleAccept() {
    await acceptDiff(run.id, allSelected ? [] : [...selected]);
  }

  async function handleReject() {
    await rejectDiff(run.id, rejectFeedback);
    setShowReject(false);
    setRejectFeedback('');
  }

  return (
    <div className="cf-diff-review">
      {/* File list (left column) */}
      <div className="cf-diff-files">
        <div className="cf-diff-files-header">
          <label className="cf-checkbox-row">
            <input type="checkbox" checked={allSelected}
              onChange={() => setSelected(allSelected ? new Set() : new Set(run.files.map(f => f.path)))} />
            <span>{run.files.length} file{run.files.length !== 1 ? 's' : ''}</span>
          </label>
        </div>

        {run.files.map(f => (
          <FileRow
            key={f.path}
            file={f}
            checked={selected.has(f.path)}
            active={activeFile === f.path}
            onToggle={() => toggleFile(f.path)}
            onSelect={() => setActiveFile(f.path)}
          />
        ))}

        {/* Action row */}
        <div className="cf-diff-actions">
          {run.status === 'pr_opened' ? (
            <div className="cf-pr-success">
              <IconCheck size={14} /> PR opened
              {run.pr_url && (
                <a href={run.pr_url} target="_blank" rel="noreferrer" className="cf-pr-link">
                  View PR <IconArrowUpRight size={12} />
                </a>
              )}
            </div>
          ) : (
            <>
              <button
                className="cf-btn cf-btn-accept"
                onClick={handleAccept}
                disabled={selected.size === 0}
              >
                <IconCheckMark size={13} /> Accept {selected.size < run.files.length ? `(${selected.size})` : 'all'} & open PR
              </button>
              <button
                className="cf-btn cf-btn-reject"
                onClick={() => setShowReject(s => !s)}
              >
                <IconXMark size={13} /> Reject
              </button>
            </>
          )}
        </div>

        {showReject && (
          <div className="cf-reject-form">
            <textarea
              className="cf-reject-input"
              placeholder="What should change? CloudForge will re-run with this feedback…"
              value={rejectFeedback}
              onChange={e => setRejectFeedback(e.target.value)}
              rows={3}
            />
            <button className="cf-btn cf-btn-danger" onClick={handleReject}>
              Send feedback
            </button>
          </div>
        )}
      </div>

      {/* Diff viewer (right column) */}
      <div className="cf-diff-viewer">
        {activeFileData && <DiffViewer file={activeFileData} />}
      </div>
    </div>
  );
}

function FileRow({ file, checked, active, onToggle, onSelect }: {
  file: GeneratedFile;
  checked: boolean;
  active: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {

  const scansPassed = file.checkov?.passed !== false && file.tfsec?.passed !== false && file.actionlint?.passed !== false;

  return (
    <div className={`cf-file-row ${active ? 'cf-file-row--active' : ''}`} onClick={onSelect}>
      <input type="checkbox" checked={checked} onClick={e => e.stopPropagation()} onChange={onToggle} />
      <span className="cf-file-icon"><AgentIcon agent={file.agent} size={12} /></span>
      <span className="cf-file-path">{file.path}</span>
      <span className={`cf-scan-dot ${scansPassed ? 'cf-scan-dot--pass' : 'cf-scan-dot--fail'}`} />
    </div>
  );
}

function DiffViewer({ file }: { file: GeneratedFile }) {
  const [view, setView] = useState<'diff' | 'full'>('diff');

  const scanBadges = [
    file.checkov && { label: 'checkov', passed: file.checkov.passed,
      detail: `${file.checkov.passed_checks} passed / ${file.checkov.failed_checks} failed` },
    file.tfsec && { label: 'tfsec', passed: file.tfsec.passed,
      detail: `${file.tfsec.total} findings` },
    file.actionlint && { label: 'actionlint', passed: file.actionlint.passed, detail: '' },
    file.pint && { label: 'pint', passed: file.pint.passed, detail: '' },
  ].filter(Boolean) as { label: string; passed: boolean; detail: string }[];

  return (
    <div className="cf-diff-file-viewer">
      <div className="cf-diff-file-header">
        <span className="cf-diff-file-path">{file.path}</span>
        <div className="cf-scan-badges">
          {scanBadges.map(b => (
            <span key={b.label} className={`cf-scan-badge ${b.passed ? 'cf-scan-badge--pass' : 'cf-scan-badge--fail'}`}>
              {b.passed ? <IconCheckMark size={11} /> : <IconXMark size={11} />} {b.label}{b.detail ? ` · ${b.detail}` : ''}
            </span>
          ))}
        </div>
        <div className="cf-view-toggle">
          <button className={view === 'diff' ? 'active' : ''} onClick={() => setView('diff')}>Diff</button>
          <button className={view === 'full' ? 'active' : ''} onClick={() => setView('full')}>Full</button>
        </div>
      </div>

      <div className="cf-diff-code">
        {view === 'diff' ? (
          <DiffLines diff={file.diff} />
        ) : (
          <pre className="cf-code-full">{file.content}</pre>
        )}
      </div>

      {/* Checkov critical findings */}
      {file.checkov?.critical_findings?.length ? (
        <div className="cf-checkov-findings">
          <div className="cf-findings-header">Checkov findings requiring review</div>
          {(file.checkov.critical_findings as any[]).slice(0, 5).map((f, i) => (
            <div key={i} className="cf-finding-row">
              <span className="cf-finding-id">{f.check_id}</span>
              <span className="cf-finding-desc">{f.check_summary ?? f.check_id}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function DiffLines({ diff }: { diff: string }) {
  if (!diff) return <div className="cf-diff-empty-msg">No diff available</div>;

  return (
    <div className="cf-diff-lines">
      {diff.split('\n').map((line, i) => {
        let cls = 'cf-diff-line';
        if (line.startsWith('+')) cls += ' cf-diff-add';
        else if (line.startsWith('-')) cls += ' cf-diff-del';
        else if (line.startsWith('@@')) cls += ' cf-diff-hunk';
        else if (line.startsWith('diff')) cls += ' cf-diff-header';
        else cls += ' cf-diff-ctx';
        return <div key={i} className={cls}><span className="cf-diff-lnum">{i + 1}</span>{line}</div>;
      })}
    </div>
  );
}
