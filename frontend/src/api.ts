import type { Activity, AgentMode, AgentRun, AppConfig, BitbucketConfig, ChangedFile, CommandInfo, FolderListing, GitStatus, Goal, JournalEntry, McpOverview, McpServer,
  McpTestResult, ModInfo, PullRequest, ReverseStatus, ServerEvent, Session, SessionSummary, SkillDetail, SkillSummary } from './types';

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
  createSession: (agent: AgentMode, directory = '', autoMode: boolean | null = null) =>
    req<Session>('POST', '/api/sessions', { agent, directory, autoMode }),
  listFolder: (path = '') => req<FolderListing>('GET', `/api/fs/list?path=${encodeURIComponent(path)}`),
  createProject: (parent: string, name: string, gitInit: boolean) => req<{ path: string }>('POST', '/api/projects', { parent, name, gitInit }),
  git: (id: string) => req<GitStatus>('GET', `/api/sessions/${id}/git`),
  createPr: (id: string, body: { title: string; description: string; branch: string; target: string; commitMessage: string }) =>
    req<PullRequest>('POST', `/api/sessions/${id}/pr`, body),
  testBitbucket: (bitbucket: Partial<BitbucketConfig>, directory = '') =>
    req<{ ok: boolean; message?: string; name?: string; kind?: string }>('POST', '/api/bitbucket/test', { bitbucket, directory }),
  session: (id: string) => req<Session>('GET', `/api/sessions/${id}`),
  patchSession: (id: string, patch: { title?: string; agent?: AgentMode; autoMode?: boolean | null; mcpDisabled?: string[] }) =>
    req<SessionSummary>('PATCH', `/api/sessions/${id}`, patch),
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

  // activity and reports
  activity: (id: string) => req<Activity>('GET', `/api/sessions/${id}/activity`),
  reportUrl: (id: string, format: 'pdf' | 'md', reasoning: 'summary' | 'full' | 'none' = 'summary', tools = true) =>
    `/api/sessions/${id}/report.${format}?reasoning=${reasoning}&tools=${tools}`,

  // slash commands from mods and skills
  commands: (directory = '') => req<CommandInfo[]>('GET', `/api/commands?directory=${encodeURIComponent(directory)}`),

  // MCP
  mcp: (directory = '') => req<McpOverview>('GET', `/api/mcp?directory=${encodeURIComponent(directory)}`),
  saveMcpServer: (name: string, server: Partial<McpServer>, renameFrom?: string) =>
    req<McpServer>('PUT', `/api/mcp/servers/${encodeURIComponent(name)}`, { ...server, renameFrom }),
  deleteMcpServer: (name: string) => req<{ ok: boolean }>('DELETE', `/api/mcp/servers/${encodeURIComponent(name)}`),
  importMcp: (text: string) => req<{ imported: string[] }>('POST', '/api/mcp/import', { text }),
  testMcp: (name: string, server?: Partial<McpServer>, directory = '') => req<McpTestResult>('POST', '/api/mcp/test', { name, server, directory }),
  restartMcp: (name: string) => req<{ closed: number }>('POST', `/api/mcp/restart/${encodeURIComponent(name)}`),
  approveMcp: (directory: string, name: string, approved: boolean) => req<{ ok: boolean }>('POST', '/api/mcp/project/approve', { directory, name, approved }),

  // skills
  skills: (directory = '') => req<SkillSummary[]>('GET', `/api/skills?directory=${encodeURIComponent(directory)}`),
  skill: (name: string, directory = '') => req<SkillDetail>('GET', `/api/skills/${encodeURIComponent(name)}?directory=${encodeURIComponent(directory)}`),
  skillFile: (name: string, path: string, directory = '') =>
    req<string>('GET', `/api/skills/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}&directory=${encodeURIComponent(directory)}`, undefined, true),
  saveSkill: (body: { scope: string; name: string; content: string; files?: Record<string, string | null>; renameFrom?: string; directory?: string }) =>
    req<SkillSummary>('PUT', '/api/skills', body),
  deleteSkill: (scope: string, name: string, directory = '') =>
    req<{ ok: boolean }>('DELETE', `/api/skills/${scope}/${encodeURIComponent(name)}?directory=${encodeURIComponent(directory)}`),
  duplicateSkill: (name: string, scope: string, newName: string, directory = '') =>
    req<SkillSummary>('POST', `/api/skills/${encodeURIComponent(name)}/duplicate`, { scope, newName, directory }),
  toggleSkill: (name: string, enabled: boolean) => req<{ ok: boolean }>('POST', `/api/skills/${encodeURIComponent(name)}/toggle`, { enabled }),
  generateSkill: (request: string, existing = '') => req<{ content: string; name: string; errors: string[] }>('POST', '/api/skills/generate', { request, existing }),
  importSkills: (path: string, scope: string, directory = '') => req<{ imported: SkillSummary[] }>('POST', '/api/skills/import', { path, scope, directory }),

  // mods
  mods: () => req<ModInfo[]>('GET', '/api/mods'),
  reloadMods: () => req<ModInfo[]>('POST', '/api/mods/reload'),
  toggleMod: (name: string, enabled: boolean) => req<ModInfo[]>('POST', `/api/mods/${encodeURIComponent(name)}/toggle`, { enabled }),
  modOptions: (name: string, options: Record<string, any>) => req<ModInfo[]>('PUT', `/api/mods/${encodeURIComponent(name)}/options`, { options }),
  modSource: (name: string) => req<{ files: Record<string, string> }>('GET', `/api/mods/${encodeURIComponent(name)}/source`),
  saveModSource: (name: string, files: Record<string, string>) =>
    req<{ validation: { ok: boolean; error: string; events: string[]; commands: string[]; tools: string[] }; mods: ModInfo[] }>(
      'PUT', `/api/mods/${encodeURIComponent(name)}/source`, { files }),
  createMod: (name: string, description: string) => req<{ mods: ModInfo[] }>('POST', '/api/mods', { name, description }),
  deleteMod: (name: string) => req<ModInfo[]>('DELETE', `/api/mods/${encodeURIComponent(name)}`),

  // reverse engineering (REA)
  reverse: () => req<ReverseStatus>('GET', '/api/reverse'),
  reverseDoctor: () => req<{ command: string; exitCode: number; timedOut: boolean; output: string }>('POST', '/api/reverse/doctor'),

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
