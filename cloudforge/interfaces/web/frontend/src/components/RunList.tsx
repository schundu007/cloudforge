import { useStore } from '../store';
import { IconStatusDot } from './icons';

export function RunList() {
  const { runs, activeRunId, setActiveRun } = useStore();
  const runList = Object.values(runs).sort((a, b) => b.started_at - a.started_at);

  if (runList.length === 0) {
    return (
      <div className="cf-run-list-empty">
        No runs yet
      </div>
    );
  }

  return (
    <div className="cf-run-list">
      {runList.map(run => (
        <button
          key={run.id}
          className={`cf-run-item ${activeRunId === run.id ? 'cf-run-item--active' : ''}`}
          onClick={() => setActiveRun(run.id)}
        >
          <div className="cf-run-item-intent">{run.intent}</div>
          <div className="cf-run-item-meta">
            <StatusDot status={run.status} />
            <span className="cf-run-item-time">
              {new Date(run.started_at).toLocaleTimeString()}
            </span>
          </div>
        </button>
      ))}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  return <IconStatusDot status={status} size={9} />;
}
