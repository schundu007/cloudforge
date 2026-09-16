import { useEffect, useRef } from 'react';
import type { Run, AgentEvent } from '../store';
import {
  IconLayers, IconTarget, IconAgent, IconCheck, IconEmulator, IconFail,
  IconFix, IconDiff, IconEscalate, IconError,
  IconDoc,
} from './icons';

type IconCmp = (props: { size?: number; className?: string }) => JSX.Element;

const EVENT_CONFIG: Record<string, { Icon: IconCmp; color: string; label: (e: AgentEvent) => string }> = {
  context_loaded: {
    Icon: IconLayers,
    color: 'neutral',
    label: e => `Context loaded — ${e.tf_modules?.length ?? 0} TF modules, ${e.workflows?.length ?? 0} workflows`,
  },
  classified: {
    Icon: IconTarget,
    color: 'info',
    label: e => `Task classified as: ${e.task_type}`,
  },
  agent_start: {
    Icon: IconAgent,
    color: 'info',
    label: e => `${e.agent} agent started`,
  },
  agent_done: {
    Icon: IconCheck,
    color: 'success',
    label: e => `${e.agent} agent complete — ${(e.files ?? []).length} file(s) generated`,
  },
  emulator_start: {
    Icon: IconEmulator,
    color: 'neutral',
    label: e => `Emulator run #${(e.iteration ?? 0) + 1}`,
  },
  emulator_pass: {
    Icon: IconCheck,
    color: 'success',
    label: e => `Emulator passed — ${e.summary ?? ''}`,
  },
  emulator_fail: {
    Icon: IconFail,
    color: 'danger',
    label: e => `Emulator failed — ${e.errors?.length ?? 0} error(s)`,
  },
  fix_start: {
    Icon: IconFix,
    color: 'warn',
    label: () => 'Auto-fix generating patches…',
  },
  diff_ready: {
    Icon: IconDiff,
    color: 'success',
    label: e => `Diff ready — ${(e.files ?? []).length} file(s), ${e.fix_iterations ?? 0} fix iteration(s)`,
  },
  escalate: {
    Icon: IconEscalate,
    color: 'danger',
    label: e => `Escalation: ${e.message ?? 'max retries reached'}`,
  },
  error: {
    Icon: IconError,
    color: 'danger',
    label: e => `Error: ${e.message ?? 'unknown'}`,
  },
};

interface Props {
  run: Run;
}

export function AgentPanel({ run }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const isRunning = run.status === 'running' || run.status === 'pending';

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [run.events.length]);

  return (
    <div className="cf-agent-panel">
      {run.events.length === 0 && isRunning && (
        <div className="cf-spinner-row">
          <span className="cf-spinner" />
          <span className="cf-spinner-text">Starting agent…</span>
        </div>
      )}

      <div className="cf-event-list">
        {run.events.map((ev, i) => (
          <EventRow key={i} event={ev} isLast={i === run.events.length - 1 && isRunning} />
        ))}
      </div>

      {/* Error details */}
      {run.events
        .filter(e => e.event === 'emulator_fail' && e.errors?.length)
        .map((e, i) => (
          <ErrorBlock key={i} errors={e.errors!} />
        ))}

      {/* Failure reason — a red dot with no explanation is not a diagnosis. */}
      {run.error && (
        <div className="cf-run-error">
          <div className="cf-run-error-header">
            <IconError size={14} /> Run failed
          </div>
          <pre className="cf-run-error-body">{run.error}</pre>
          {/Could not resolve authentication|x-api-key|api_key/i.test(run.error) && (
            <p className="cf-run-error-hint">
              The API has no Anthropic key. Set ANTHROPIC_API_KEY on the server and redeploy.
            </p>
          )}
        </div>
      )}

      {/* Escalation block */}
      {run.status === 'escalated' && (
        <div className="cf-escalation">
          <div className="cf-escalation-header">
            <IconEscalate size={14} /> Human Review Required
          </div>
          <p>The auto-fix loop exhausted its retry budget. Review the errors below and re-run with a more specific intent.</p>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}

function EventRow({ event, isLast }: { event: AgentEvent; isLast: boolean }) {
  const cfg = EVENT_CONFIG[event.event] ?? {
    Icon: IconDoc, color: 'neutral', label: (e: AgentEvent) => e.event,
  };
  const label = cfg.label(event);
  const ts = event.timestamp ? new Date(event.timestamp).toLocaleTimeString([], { hour12: false }) : '';

  return (
    <div className={`cf-event cf-event--${cfg.color} ${isLast ? 'cf-event--active' : ''}`}>
      <span className="cf-event-icon"><cfg.Icon size={14} /></span>
      <span className="cf-event-label">{label}</span>
      {ts && <span className="cf-event-ts">{ts}</span>}
      {isLast && <span className="cf-pulse" />}
    </div>
  );
}

function ErrorBlock({ errors }: { errors: AgentEvent['errors'] }) {
  if (!errors?.length) return null;
  return (
    <div className="cf-error-block">
      {errors.slice(0, 5).map((err, i) => (
        <div key={i} className="cf-error-row">
          <span className="cf-error-type">{err.type}</span>
          <span className="cf-error-file">{err.file ?? ''}</span>
          <span className="cf-error-msg">{err.message?.slice(0, 200)}</span>
        </div>
      ))}
      {errors.length > 5 && (
        <div className="cf-error-more">+{errors.length - 5} more errors</div>
      )}
    </div>
  );
}
