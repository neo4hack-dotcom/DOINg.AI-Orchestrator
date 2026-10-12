import type { AgentMode, AgentRun, AppConfig, ChangedFile, Goal, JournalEntry, ServerEvent, Session, SessionSummary } from './types';

async function req<T>(method: string, url: string, body?: unknown, text = false): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (text ? res.text() : res.json()) as Promise<T>;
}

export const api = {
  config: () => req<AppConfig>('GET', '/api/config'),
  saveConfig: (cfg: Partial<AppConfig>) => req<AppConfig>('PUT', '/api/config', cfg),
  testLlm: (llm?: Partial<AppConfig['llm']>) =>
    req<{ ok: boolean; models?: string[]; message: string }>('POST', '/api/config/test', { llm }),
  importNexus: (path: string) => req<{ config: AppConfig; source: string }>('POST', '/api/config/import-nexus', { path }),

  sessions: () => req<SessionSummary[]>('GET', '/api/sessions'),
  createSession: (agent: AgentMode, directory = '') => req<Session>('POST', '/api/sessions', { agent, directory }),
  session: (id: string) => req<Session>('GET', `/api/sessions/${id}`),
  patchSession: (id: string, patch: { title?: string; agent?: AgentMode; autonomous?: boolean }) => req<SessionSummary>('PATCH', `/api/sessions/${id}`, patch),
  deleteSession: (id: string) => req<{ ok: boolean }>('DELETE', `/api/sessions/${id}`),
  send: (id: string, text: string, agent: AgentMode) => req<{ ok: boolean }>('POST', `/api/sessions/${id}/message`, { text, agent }),
  abort: (id: string) => req<{ ok: boolean }>('POST', `/api/sessions/${id}/abort`),
  permission: (id: string, pid: string, response: 'once' | 'always' | 'reject') =>
    req<{ ok: boolean }>('POST', `/api/sessions/${id}/permissions/${pid}`, { response }),
  approveFlow: (id: string) => req<{ ok: boolean }>('POST', `/api/sessions/${id}/flow/approve`),
  files: (id: string, q: string) => req<string[]>('GET', `/api/sessions/${id}/files?q=${encodeURIComponent(q)}`),
  run: (id: string, runId: string) => req<AgentRun>('GET', `/api/sessions/${id}/runs/${runId}`),
  answer: (id: string, qid: string, answers: string[][]) => req<{ ok: boolean }>('POST', `/api/sessions/${id}/questions/${qid}`, { answers }),
  changes: (id: string) => req<{ base: string | null; files: ChangedFile[] }>('GET', `/api/sessions/${id}/changes`),
  diff: (id: string, path: string) => req<string>('GET', `/api/sessions/${id}/diff?path=${encodeURIComponent(path)}`, undefined, true),
  revertFile: (id: string, path: string) => req<{ files: string[] }>('POST', `/api/sessions/${id}/revert-file`, { path }),
  undo: (id: string) => req<{ text: string; files: string[] }>('POST', `/api/sessions/${id}/undo`),
  journal: (id: string, limit = 1500) => req<JournalEntry[]>('GET', `/api/sessions/${id}/journal?limit=${limit}`),
  report: (id: string) => req<string>('GET', `/api/sessions/${id}/report`, undefined, true),
  fork: (id: string, messageId?: string) => req<Session>('POST', `/api/sessions/${id}/fork`, { messageId }),
  compact: (id: string) => req<{ ok: boolean }>('POST', `/api/sessions/${id}/compact`),
  goal: (id: string, condition: string | null) => req<{ goal: Goal | null }>('POST', `/api/sessions/${id}/goal`, { condition }),

  subscribe(id: string, onEvent: (e: ServerEvent) => void): () => void {
    const es = new EventSource(`/api/sessions/${id}/events`);
    es.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch {
        /* ignore malformed frames */
      }
    };
    return () => es.close();
  },
};
