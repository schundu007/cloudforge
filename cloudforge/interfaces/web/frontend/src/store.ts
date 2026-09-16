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
  repoUrl: string;

  // Actions
  setApiUrl: (url: string) => void;
  setRepoRoot: (root: string) => void;
  setRepoUrl: (url: string) => void;
  startRun: (intent: string) => Promise<string>;
  streamRun: (runId: string) => void;
  acceptDiff: (runId: string, filePaths?: string[]) => Promise<void>;
  rejectDiff: (runId: string, feedback: string) => Promise<void>;
  setActiveRun: (runId: string | null) => void;
}

/**
 * Shared-secret sent as `Authorization: Bearer` when the API enforces a gate.
 *
 * NOTE: anything in a VITE_ variable is compiled into the client bundle and is
 * therefore readable by anyone who loads the page. This deters scanners and
 * direct API abuse; it is not a substitute for real user authentication.
 */
/** In-flight SSE readers, so a stream can be cancelled on completion. */
const streamControllers = new Map<string, AbortController>();

const API_TOKEN = (import.meta.env?.VITE_API_TOKEN as string) ?? '';

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return API_TOKEN ? { ...extra, Authorization: `Bearer ${API_TOKEN}` } : extra;
}

export const useStore = create<CloudForgeStore>((set, get) => ({
  runs: {},
  activeRunId: null,
  // Empty default => same-origin relative URLs, which go through the Vite
  // dev proxy (see vite.config.ts). Override with VITE_API_URL for a
  // backend on another host/port.
  apiUrl: (import.meta.env?.VITE_API_URL as string) ?? '',
  repoRoot: '.',
  // A deployed API has no repository of its own; set this to clone a target
  // repo for each run.
  repoUrl: (import.meta.env?.VITE_DEFAULT_REPO_URL as string) ?? '',

  setApiUrl: (url) => set({ apiUrl: url }),
  setRepoRoot: (root) => set({ repoRoot: root }),
  setRepoUrl: (url) => set({ repoUrl: url }),
  setActiveRun: (runId) => set({ activeRunId: runId }),

  startRun: async (intent: string) => {
    const { apiUrl, repoRoot, repoUrl } = get();
    const resp = await fetch(`${apiUrl}/runs`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      // repo_url wins server-side; send repo_root only as the local fallback.
      body: JSON.stringify(
        repoUrl.trim()
          ? { intent, repo_url: repoUrl.trim() }
          : { intent, repo_root: repoRoot },
      ),
    });
    if (!resp.ok) {
      const detail = await resp.text();
      throw new Error(`Could not start run (${resp.status}): ${detail.slice(0, 200)}`);
    }
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
    const controller = new AbortController();
    streamControllers.set(runId, controller);

    const applyEvent = (event: AgentEvent): boolean => {
      let finished = false;
      set((s) => {
        const run = s.runs[runId];
        if (!run) return s;

        const updated: Run = { ...run, events: [...run.events, event] };

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
          updated.status = (event as { status?: Run['status'] }).status ?? 'complete';
          finished = true;
        } else if (event.event === 'error') {
          updated.status = 'error';
          updated.completed_at = Date.now();
          finished = true;
        } else if (event.event === 'context_loaded' || event.event === 'agent_start') {
          updated.status = 'running';
        }

        return { runs: { ...s.runs, [runId]: updated } };
      });
      return finished;
    };

    const fail = () => {
      set((s) => {
        const run = s.runs[runId];
        if (!run || run.status === 'complete' || run.status === 'pr_opened') return s;
        return { runs: { ...s.runs, [runId]: { ...run, status: 'error' } } };
      });
    };

    // EventSource cannot send an Authorization header, so read the SSE stream
    // over fetch instead and parse the frames by hand.
    (async () => {
      try {
        const resp = await fetch(`${apiUrl}/runs/${runId}/stream`, {
          headers: authHeaders({ Accept: 'text/event-stream' }),
          signal: controller.signal,
        });
        if (!resp.ok || !resp.body) {
          fail();
          return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by a blank line.
          for (;;) {
            const boundary = /\r?\n\r?\n/.exec(buffer);
            if (!boundary) break;

            const frame = buffer.slice(0, boundary.index);
            buffer = buffer.slice(boundary.index + boundary[0].length);

            const data = frame
              .split(/\r?\n/)
              .filter((l) => l.startsWith('data:'))
              .map((l) => l.slice(5).trim())
              .join('\n');
            if (!data) continue;

            let parsed: AgentEvent;
            try {
              parsed = JSON.parse(data);
            } catch {
              continue;
            }
            if (applyEvent({ ...parsed, timestamp: Date.now() })) {
              controller.abort();
              return;
            }
          }
        }
      } catch (err) {
        if ((err as Error)?.name !== 'AbortError') fail();
      } finally {
        streamControllers.delete(runId);
      }
    })();
  },

  acceptDiff: async (runId: string, filePaths?: string[]) => {
    const { apiUrl } = get();
    const resp = await fetch(`${apiUrl}/runs/${runId}/accept`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
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
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ message: feedback }),
    });
    set((s) => ({
      runs: { ...s.runs, [runId]: { ...s.runs[runId], status: 'pending' } },
    }));
  },
}));
