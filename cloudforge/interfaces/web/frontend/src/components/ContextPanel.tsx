import { Run, type GeneratedFile } from '../store';

/** A generated file has no status field; derive one from its scan results. */
function scanState(file: GeneratedFile): string {
  const scans = [file.checkov?.passed, file.tfsec?.passed, file.actionlint?.passed, file.pint?.passed];
  if (scans.some(v => v === false)) return 'failed';
  if (scans.some(v => v === true)) return 'passed';
  return 'generated';
}

interface ContextPanelProps {
  run: Run;
}

export function ContextPanel({ run }: ContextPanelProps) {
  return (
    <div className="cf-context-panel">
      <h3>Infrastructure Context</h3>

      <section className="cf-context-section">
        <h4>Run Details</h4>
        <dl className="cf-context-dl">
          <dt>Run ID</dt>
          <dd><code>{run.id}</code></dd>
          <dt>Status</dt>
          <dd>{run.status}</dd>
          <dt>Started</dt>
          <dd>{new Date(run.started_at).toLocaleString()}</dd>
          {run.completed_at && (
            <>
              <dt>Completed</dt>
              <dd>{new Date(run.completed_at).toLocaleString()}</dd>
            </>
          )}
          <dt>Fix Iterations</dt>
          <dd>{run.fix_iterations}</dd>
        </dl>
      </section>

      {run.files.length > 0 && (
        <section className="cf-context-section">
          <h4>Modified Files ({run.files.length})</h4>
          <ul className="cf-file-list">
            {run.files.map(file => (
              <li key={file.path}>
                <code>{file.path}</code>
                <span className={`cf-file-status cf-file-status--${scanState(file)}`}>
                  {scanState(file)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {run.pr_url && (
        <section className="cf-context-section">
          <h4>Pull Request</h4>
          <a href={run.pr_url} target="_blank" rel="noopener noreferrer" className="cf-pr-link">
            {run.pr_url}
          </a>
        </section>
      )}
    </div>
  );
}
