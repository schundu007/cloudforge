/**
 * CloudForge Dashboard — main app store + entry point
 * Zustand store manages all agent runs, SSE connections, and diff state.
 */
import { create } from 'zustand';

export type EventType =
  | 'context_loaded' | 'classified' | 'agent_start' | 'agent_done'
  | 'emulator_start' | 'emulator_pass' | 'emulator_fail'
  | 'fix_start' | 'diff_ready' | 'escalate' | 'done' | 'error';

export interface AgentEvent {
  event: EventType;
  agent?: string;
  task_type?: string;
  summary?: string;
  errors?: Array<{ type: string; message: string; file?: string }>;
  files?: GeneratedFile[];
  diff?: string;
  fix_iterations?: number;
  message?: string;
  iteration?: number;
  tf_modules?: string[];
  workflows?: string[];
  timestamp?: number;
}

export interface GeneratedFile {
  path: string;
  content: string;
  diff: string;
  agent: 'pipeline' | 'iac' | 'security' | 'observability';
  checkov?: { passed: boolean; passed_checks: number; failed_checks: number; critical_findings: unknown[] };
  tfsec?: { passed: boolean; findings: unknown[]; total: number };
  actionlint?: { passed: boolean; output: string };
  pint?: { passed: boolean; output: string };
}

export interface Run {
  id: string;
  intent: string;
  status: 'pending' | 'running' | 'complete' | 'error' | 'escalated' | 'pr_opened';
  events: AgentEvent[];
  files: GeneratedFile[];
  diff: string | null;
  pr_url: string | null;
  fix_iterations: number;
  started_at: number;
  completed_at?: number;
}

interface CloudForgeStore {
  runs: Record<string, Run>;
  activeRunId: string | null;
  apiUrl: string;
  repoRoot: string;

  // Actions
  setApiUrl: (url: string) => void;
  setRepoRoot: (root: string) => void;
  startRun: (intent: string) => Promise<string>;
  streamRun: (runId: string) => void;
  acceptDiff: (runId: string, filePaths?: string[]) => Promise<void>;
  rejectDiff: (runId: string, feedback: string) => Promise<void>;
  setActiveRun: (runId: string | null) => void;
}

export const useStore = create<CloudForgeStore>((set, get) => ({
  runs: {},
  activeRunId: null,
  apiUrl: 'http://localhost:8000',
  repoRoot: '.',

  setApiUrl: (url) => set({ apiUrl: url }),
  setRepoRoot: (root) => set({ repoRoot: root }),
  setActiveRun: (runId) => set({ activeRunId: runId }),

  startRun: async (intent: string) => {
    const { apiUrl, repoRoot } = get();
    const resp = await fetch(`${apiUrl}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intent, repo_root: repoRoot }),
    });
    const data = await resp.json();
    const runId: string = data.run_id;

    const run: Run = {
      id: runId,
      intent,
      status: 'pending',
      events: [],
      files: [],
      diff: null,
      pr_url: null,
      fix_iterations: 0,
      started_at: Date.now(),
    };

    set((s) => ({ runs: { ...s.runs, [runId]: run }, activeRunId: runId }));
    get().streamRun(runId);
    return runId;
  },

  streamRun: (runId: string) => {
    const { apiUrl } = get();
    const es = new EventSource(`${apiUrl}/runs/${runId}/stream`);

    es.onmessage = (ev) => {
      const event: AgentEvent = { ...JSON.parse(ev.data), timestamp: Date.now() };

      set((s) => {
        const run = s.runs[runId];
        if (!run) return s;

        const updated: Run = {
          ...run,
          events: [...run.events, event],
        };

        if (event.event === 'diff_ready') {
          updated.files = event.files ?? [];
          updated.diff = event.diff ?? null;
          updated.fix_iterations = event.fix_iterations ?? 0;
          updated.status = 'complete';
          updated.completed_at = Date.now();
        } else if (event.event === 'escalate') {
          updated.status = 'escalated';
          updated.completed_at = Date.now();
        } else if (event.event === 'done') {
          updated.status = (event as any).status ?? 'complete';
          es.close();
        } else if (event.event === 'error') {
          updated.status = 'error';
          updated.completed_at = Date.now();
          es.close();
        } else if (event.event === 'context_loaded' || event.event === 'agent_start') {
          updated.status = 'running';
        }

        return { runs: { ...s.runs, [runId]: updated } };
      });
    };

    es.onerror = () => {
      set((s) => {
        const run = s.runs[runId];
        if (!run || run.status === 'complete') return s;
        return { runs: { ...s.runs, [runId]: { ...run, status: 'error' } } };
      });
      es.close();
    };
  },

  acceptDiff: async (runId: string, filePaths?: string[]) => {
    const { apiUrl } = get();
    const resp = await fetch(`${apiUrl}/runs/${runId}/accept`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId, file_paths: filePaths ?? [] }),
    });
    const data = await resp.json();
    set((s) => ({
      runs: {
        ...s.runs,
        [runId]: { ...s.runs[runId], status: 'pr_opened', pr_url: data.pr_url },
      },
    }));
  },

  rejectDiff: async (runId: string, feedback: string) => {
    const { apiUrl } = get();
    await fetch(`${apiUrl}/runs/${runId}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: feedback }),
    });
    set((s) => ({
      runs: { ...s.runs, [runId]: { ...s.runs[runId], status: 'pending' } },
    }));
  },
}));
