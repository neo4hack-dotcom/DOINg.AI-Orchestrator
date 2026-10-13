import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ClipboardPaste, Loader2, Pencil, Plug, Plus, RotateCw, ShieldCheck, Trash2, X, XCircle } from 'lucide-react';
import type { AppConfig, McpOverview, McpPreset, McpServer, McpTestResult } from '../types';
import { api } from '../api';
import { cx } from '../util';

const inputCls = 'w-full rounded border border-line bg-bg px-2.5 py-1.5 text-fg outline-none focus:border-accent placeholder:text-faint';

const toLines = (o?: Record<string, string>, sep = '=') => Object.entries(o || {}).map(([k, v]) => `${k}${sep}${v}`).join('\n');
const fromLines = (text: string, sep: string | RegExp = '='): Record<string, string> => {
  const out: Record<string, string> = {};
  text.split('\n').forEach((line) => {
    const m = typeof sep === 'string' ? line.split(sep) : line.split(sep);
    if (m.length >= 2 && m[0].trim()) out[m[0].trim()] = m.slice(1).join(typeof sep === 'string' ? sep : ':').trim();
  });
  return out;
};
/* "npx -y pkg --flag 'a b'" -> ["npx", "-y", "pkg", "--flag", "a b"] */
const splitArgs = (text: string): string[] => (text.match(/"[^"]*"|'[^']*'|\S+/g) || []).map((a) => a.replace(/^["']|["']$/g, ''));
const joinArgs = (args: string[]) => args.map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(' ');

type Draft = { name: string; renameFrom?: string; type: 'stdio' | 'http' | 'sse'; commandLine: string; envText: string; cwd: string; url: string;
  headersText: string; timeout: number; permission: McpServer['permission']; trustReadOnly: boolean; description: string; enabled: boolean;
  disabledTools: string[] };

const emptyDraft: Draft = { name: '', type: 'stdio', commandLine: '', envText: '', cwd: '', url: '', headersText: '', timeout: 120, permission: 'ask',
  trustReadOnly: true, description: '', enabled: true, disabledTools: [] };

function toDraft(s: Partial<McpServer> & { name: string }, rename = true): Draft {
  return {
    name: s.name, renameFrom: rename ? s.name : undefined, type: s.type || (s.url ? 'http' : 'stdio'),
    commandLine: s.command ? joinArgs([s.command, ...(s.args || [])]) : '', envText: toLines(s.env), cwd: s.cwd || '', url: s.url || '',
    headersText: toLines(s.headers, ': '), timeout: s.timeout || 120, permission: s.permission || 'ask', trustReadOnly: s.trustReadOnly ?? true,
    description: s.description || '', enabled: s.enabled ?? true, disabledTools: s.disabledTools || [],
  };
}

function fromDraft(d: Draft): Partial<McpServer> {
  const [command, ...args] = splitArgs(d.commandLine);
  const base = { type: d.type, enabled: d.enabled, permission: d.permission, trustReadOnly: d.trustReadOnly, timeout: d.timeout,
    description: d.description, disabledTools: d.disabledTools };
  return d.type === 'stdio'
    ? { ...base, command, args, env: fromLines(d.envText), cwd: d.cwd || undefined }
    : { ...base, url: d.url, headers: fromLines(d.headersText, /:\s*/) };
}

function Status({ live }: { live?: McpServer['live'] }) {
  const color = !live ? 'bg-faint' : live.status === 'connected' ? 'bg-ok' : live.status === 'error' ? 'bg-err' : 'bg-warn';
  return <span className={cx('inline-block h-2 w-2 shrink-0 rounded-full', color)} title={live ? `${live.status}${live.error ? `: ${live.error}` : ''}` : 'not started (connects on first use)'} />;
}

/* Settings > MCP: servers (Claude Desktop / Code format), presets, JSON import, test, project approval, best-practice options. */
export function McpSettings({ cfg, setCfg, directory }: { cfg: AppConfig; setCfg: (fn: (c: AppConfig) => AppConfig) => void; directory: string }) {
  const [data, setData] = useState<McpOverview | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [tests, setTests] = useState<Record<string, McpTestResult | 'running'>>({});
  const [importText, setImportText] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => api.mcp(directory).then(setData).catch((e) => setMsg({ ok: false, text: e.message })), [directory]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!draft) return;
    try {
      await api.saveMcpServer(draft.name.trim(), fromDraft(draft), draft.renameFrom);
      setDraft(null);
      setMsg({ ok: true, text: `Saved ${draft.name}.` });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    }
  };

  const test = async (name: string, server?: Partial<McpServer>) => {
    setTests((t) => ({ ...t, [name]: 'running' }));
    try {
      const r = await api.testMcp(name, server, directory);
      setTests((t) => ({ ...t, [name]: r }));
    } catch (e: any) {
      setTests((t) => ({ ...t, [name]: { ok: false, status: 'error', error: e.message, server: {}, tools: [], resources: false, log: [] } }));
    }
  };

  const toggleTool = async (s: McpServer, tool: string) => {
    const disabled = new Set(s.disabledTools || []);
    disabled.has(tool) ? disabled.delete(tool) : disabled.add(tool);
    await api.saveMcpServer(s.name, { ...s, disabledTools: [...disabled] }, s.name).catch((e) => setMsg({ ok: false, text: e.message }));
    load();
  };

  const doImport = async () => {
    try {
      const r = await api.importMcp(importText);
      setMsg({ ok: true, text: `Imported: ${r.imported.join(', ')}` });
      setImportText('');
      setImportOpen(false);
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    }
  };

  const addPreset = (p: McpPreset) => setDraft(toDraft({
    name: p.name, type: (p.type as McpServer['type']) || (p.url ? 'http' : 'stdio'), command: p.command, args: p.args, url: p.url, headers: p.headers,
    description: p.description, enabled: true, permission: 'ask', trustReadOnly: true,
  }, false));

  const setMcp = (patch: Partial<AppConfig['mcp']>) => setCfg((c) => ({ ...c, mcp: { ...c.mcp, ...patch } }));

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-accent/30 bg-accent/5 p-3 text-[12px] text-muted">
        <div className="mb-1 flex items-center gap-1.5 font-semibold text-accent"><Plug size={13} /> Model Context Protocol</div>
        Connect tools, data and services (docs, tickets, databases, browsers...) to every coding session. Servers start on first use;
        tools are namespaced <code>mcp__server__tool</code>, read-only tools are trusted, others ask (or follow auto mode), and beyond the
        threshold below the agents search tools on demand instead of loading every schema - the best fit for local models.
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1">
          <span className="text-[12px] text-muted">Default permission for MCP tools</span>
          <select className={inputCls} value={cfg.permissions.mcp} onChange={(e) => setCfg((c) => ({ ...c, permissions: { ...c.permissions, mcp: e.target.value as AppConfig['permissions']['mcp'] } }))}>
            <option value="ask">ask</option><option value="allow">allow</option><option value="deny">deny</option>
          </select>
        </label>
        <label className="block space-y-1">
          <span className="text-[12px] text-muted">Tool search above (tools; 0 = always list every tool)</span>
          <input className={inputCls} type="number" min={0} max={500} value={cfg.mcp.deferThreshold} onChange={(e) => setMcp({ deferThreshold: Number(e.target.value) })} />
        </label>
        <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" className="accent-[var(--accent)]" checked={cfg.mcp.inPlanMode} onChange={(e) => setMcp({ inPlanMode: e.target.checked })} /> Read-only MCP tools for the plan agent and the orchestrator</label>
        <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" className="accent-[var(--accent)]" checked={cfg.mcp.inWorkers} onChange={(e) => setMcp({ inWorkers: e.target.checked })} /> MCP tools for savvy workers</label>
      </div>
      <p className="text-[11px] text-faint">These four options are saved with the Save button; servers below are saved immediately.</p>

      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">Servers</span>
        <button type="button" onClick={() => setDraft({ ...emptyDraft })} className="inline-flex items-center gap-1 rounded border border-line px-2 py-1 text-[12px] hover:border-accent/60"><Plus size={12} /> Add server</button>
        <button type="button" onClick={() => setImportOpen((o) => !o)} className="inline-flex items-center gap-1 rounded border border-line px-2 py-1 text-[12px] hover:border-accent/60"><ClipboardPaste size={12} /> Import JSON</button>
        <button type="button" onClick={load} className="ml-auto rounded p-1 text-faint hover:text-fg" title="Refresh"><RotateCw size={13} /></button>
      </div>
      {msg && <div className={cx('rounded px-2 py-1 text-[12px]', msg.ok ? 'bg-ok/10 text-ok' : 'bg-err/10 text-err')}>{msg.text}</div>}

      {importOpen && (
        <div className="space-y-2 rounded-md border border-line p-3">
          <div className="text-[12px] text-muted">Paste a Claude Desktop / Claude Code (<code>{'{"mcpServers": {...}}'}</code>), VS Code (<code>{'{"servers": {...}}'}</code>) or single-server JSON.</div>
          <textarea className={`${inputCls} min-h-[110px] font-mono text-[12px]`} value={importText} onChange={(e) => setImportText(e.target.value)}
            placeholder={'{\n  "mcpServers": {\n    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] }\n  }\n}'} />
          <button type="button" onClick={doImport} disabled={!importText.trim()} className="rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg disabled:opacity-40">Import</button>
        </div>
      )}

      {draft && (
        <div className="space-y-2 rounded-md border border-accent/40 bg-panel p-3">
          <div className="flex items-center gap-2 font-semibold">{draft.renameFrom ? `Edit ${draft.renameFrom}` : 'New MCP server'}
            <button type="button" onClick={() => setDraft(null)} className="ml-auto text-faint hover:text-fg"><X size={14} /></button></div>
          <div className="grid gap-2 sm:grid-cols-3">
            <input className={inputCls} placeholder="name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <select className={inputCls} value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value as Draft['type'] })}>
              <option value="stdio">stdio (local command)</option><option value="http">Streamable HTTP</option><option value="sse">SSE (legacy)</option>
            </select>
            <select className={inputCls} value={draft.permission} onChange={(e) => setDraft({ ...draft, permission: e.target.value as Draft['permission'] })}>
              <option value="ask">ask (follow the default)</option><option value="allow">allow all its tools</option><option value="deny">deny all its tools</option>
            </select>
          </div>
          {draft.type === 'stdio' ? (
            <>
              <input className={`${inputCls} font-mono`} placeholder='command and arguments, e.g. npx -y @modelcontextprotocol/server-memory' value={draft.commandLine}
                onChange={(e) => setDraft({ ...draft, commandLine: e.target.value })} />
              <div className="grid gap-2 sm:grid-cols-2">
                <textarea className={`${inputCls} min-h-[60px] font-mono text-[12px]`} placeholder={'environment, one per line\nAPI_KEY=...'} value={draft.envText}
                  onChange={(e) => setDraft({ ...draft, envText: e.target.value })} />
                <input className={inputCls} placeholder="working folder (empty = the session folder)" value={draft.cwd} onChange={(e) => setDraft({ ...draft, cwd: e.target.value })} />
              </div>
            </>
          ) : (
            <>
              <input className={`${inputCls} font-mono`} placeholder="https://example.com/mcp" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
              <textarea className={`${inputCls} min-h-[50px] font-mono text-[12px]`} placeholder={'headers, one per line\nAuthorization: Bearer ...'} value={draft.headersText}
                onChange={(e) => setDraft({ ...draft, headersText: e.target.value })} />
            </>
          )}
          <div className="grid gap-2 sm:grid-cols-3">
            <input className={inputCls} placeholder="description" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            <label className="flex items-center gap-1.5 text-[12px]">Timeout (s)
              <input className={`${inputCls} w-24`} type="number" min={5} max={7200} value={draft.timeout} onChange={(e) => setDraft({ ...draft, timeout: Number(e.target.value) })} /></label>
            <label className="flex items-center gap-1.5 text-[12px]"><input type="checkbox" className="accent-[var(--accent)]" checked={draft.trustReadOnly}
              onChange={(e) => setDraft({ ...draft, trustReadOnly: e.target.checked })} /> trust read-only tools</label>
          </div>
          <p className="text-[11px] text-faint">Secrets in env / headers are stored in data/config.json and shown masked; keep the mask to leave a secret unchanged.</p>
          <div className="flex gap-2">
            <button type="button" onClick={save} disabled={!draft.name.trim() || (draft.type === 'stdio' ? !draft.commandLine.trim() : !draft.url.trim())}
              className="rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg disabled:opacity-40">Save server</button>
            <button type="button" onClick={() => test(draft.name || 'draft', fromDraft(draft))} className="rounded border border-line px-3 py-1 text-[12px] hover:border-accent/60">Test</button>
            {tests[draft.name || 'draft'] && tests[draft.name || 'draft'] !== 'running' && (
              <span className={cx('self-center text-[12px]', (tests[draft.name || 'draft'] as McpTestResult).ok ? 'text-ok' : 'text-err')}>
                {(tests[draft.name || 'draft'] as McpTestResult).ok ? `OK · ${(tests[draft.name || 'draft'] as McpTestResult).tools.length} tools` : (tests[draft.name || 'draft'] as McpTestResult).error.slice(0, 200)}
              </span>
            )}
          </div>
        </div>
      )}

      <div className="space-y-2">
        {!data?.servers.length && <div className="text-[12px] text-faint">No MCP server yet: add one, import your Claude Desktop config, or pick a preset.</div>}
        {data?.servers.map((s) => {
          const r = tests[s.name];
          return (
            <div key={s.name} className="rounded-md border border-line bg-panel px-3 py-2">
              <div className="flex min-w-0 items-center gap-2">
                <Status live={s.live} />
                <span className="shrink-0 font-semibold">{s.name}</span>
                <span className="shrink-0 rounded bg-elev px-1 text-[10px] uppercase text-faint">{s.type}</span>
                {s.source === 'reverse' && <span className="rounded bg-warn/15 px-1 text-[10px] text-warn">reverse engineering layer</span>}
                {s.live?.tools ? <span className="shrink-0 whitespace-nowrap text-[11px] text-faint">{s.live.tools} tools</span> : null}
                <span className="min-w-0 truncate font-mono text-[11px] text-faint">{s.url || joinArgs([s.command || '', ...(s.args || [])])}</span>
                <div className="ml-auto flex shrink-0 items-center gap-1">
                  {s.source !== 'reverse' && (
                    <label className="mr-1 flex items-center gap-1 text-[11px] text-muted" title="Enabled for every session">
                      <input type="checkbox" className="accent-[var(--accent)]" checked={s.enabled}
                        onChange={(e) => api.saveMcpServer(s.name, { ...s, enabled: e.target.checked }, s.name).then(load)} /> on
                    </label>
                  )}
                  <button type="button" onClick={() => test(s.name)} className="rounded border border-line px-1.5 py-0.5 text-[11px] hover:border-accent/60">
                    {r === 'running' ? <Loader2 size={11} className="spin" /> : 'Test'}
                  </button>
                  <button type="button" title="Restart" onClick={() => api.restartMcp(s.name).then(load)} className="rounded p-1 text-faint hover:text-fg"><RotateCw size={12} /></button>
                  {s.source !== 'reverse' && <button type="button" title="Edit" onClick={() => setDraft(toDraft(s))} className="rounded p-1 text-faint hover:text-fg"><Pencil size={12} /></button>}
                  {s.source !== 'reverse' && (
                    <button type="button" title="Delete" onClick={() => window.confirm(`Delete the MCP server ${s.name}?`) && api.deleteMcpServer(s.name).then(load)}
                      className="rounded p-1 text-faint hover:text-err"><Trash2 size={12} /></button>
                  )}
                </div>
              </div>
              {s.description && <div className="mt-0.5 text-[11px] text-muted">{s.description}</div>}
              {s.live?.error && <div className="mt-0.5 truncate text-[11px] text-err">{s.live.error}</div>}
              {r && r !== 'running' && (
                <div className="mt-1.5 rounded border border-line bg-bg px-2 py-1.5 text-[12px]">
                  {r.ok ? (
                    <>
                      <div className="mb-1 flex items-center gap-1 text-ok"><CheckCircle2 size={12} /> {r.server?.name} {r.server?.version} · {r.tools.length} tools{r.resources ? ' · resources' : ''}</div>
                      <div className="flex flex-wrap gap-1">
                        {r.tools.map((tool) => {
                          const off = (s.disabledTools || []).includes(tool.name);
                          return (
                            <button key={tool.name} type="button" title={`${tool.description}\n\nClick to ${off ? 'enable' : 'disable'} this tool`} onClick={() => toggleTool(s, tool.name)}
                              className={cx('rounded border px-1.5 text-[11px]', off ? 'border-line text-faint line-through' : 'border-accent/40 text-fg')}>
                              {tool.readOnly && <ShieldCheck size={10} className="mr-0.5 inline text-ok" />}{tool.name}
                            </button>
                          );
                        })}
                      </div>
                    </>
                  ) : (
                    <div className="flex items-start gap-1 text-err"><XCircle size={12} className="mt-0.5 shrink-0" /><pre className="whitespace-pre-wrap text-[11px]">{r.error}</pre></div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {data && data.project.length > 0 && (
        <div className="space-y-2 rounded-md border border-warn/40 bg-warn/5 p-3">
          <div className="text-[12px] font-semibold text-warn">Project servers ({directory}/.mcp.json)</div>
          <p className="text-[11px] text-muted">Declared by the repository: they run code on your machine, so they only start once you approve them.</p>
          {data.project.map((s) => (
            <div key={s.name} className="flex items-center gap-2 text-[12px]">
              <Status live={s.live} />
              <span className="font-semibold">{s.name}</span>
              <span className="min-w-0 truncate font-mono text-[11px] text-faint">{s.url || joinArgs([s.command || '', ...(s.args || [])])}</span>
              <button type="button" onClick={() => api.approveMcp(directory, s.name, !s.approved).then(load)}
                className={cx('ml-auto rounded px-2 py-0.5 text-[11px]', s.approved ? 'border border-line text-muted hover:text-err' : 'bg-warn font-semibold text-bg')}>
                {s.approved ? 'Revoke' : 'Approve'}
              </button>
            </div>
          ))}
        </div>
      )}

      {data && (
        <div>
          <div className="mb-1.5 font-semibold">Presets</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {data.presets.map((p) => (
              <button key={p.name} type="button" onClick={() => addPreset(p)} className="rounded-md border border-line bg-panel px-3 py-2 text-left hover:border-accent/50">
                <div className="flex items-center gap-1.5 text-[12px] font-semibold"><Plus size={11} className="text-accent" /> {p.title}</div>
                <div className="text-[11px] text-muted">{p.description}</div>
                <div className="mt-0.5 truncate font-mono text-[10px] text-faint">{p.url || joinArgs([p.command || '', ...(p.args || [])])}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
