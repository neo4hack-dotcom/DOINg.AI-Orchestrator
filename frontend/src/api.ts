import type { AppConfig, Session, SessionSummary, ServerEvent, AgentMode } from './types';

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
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
  return res.json();
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
  patchSession: (id: string, patch: { title?: string; agent?: AgentMode }) => req<SessionSummary>('PATCH', `/api/sessions/${id}`, patch),
  deleteSession: (id: string) => req<{ ok: boolean }>('DELETE', `/api/sessions/${id}`),
  send: (id: string, text: string, agent: AgentMode) => req<{ ok: boolean }>('POST', `/api/sessions/${id}/message`, { text, agent }),
  abort: (id: string) => req<{ ok: boolean }>('POST', `/api/sessions/${id}/abort`),
  permission: (id: string, pid: string, response: 'once' | 'always' | 'reject') =>
    req<{ ok: boolean }>('POST', `/api/sessions/${id}/permissions/${pid}`, { response }),
  approveFlow: (id: string) => req<{ ok: boolean }>('POST', `/api/sessions/${id}/flow/approve`),
  files: (id: string, q: string) => req<string[]>('GET', `/api/sessions/${id}/files?q=${encodeURIComponent(q)}`),

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
