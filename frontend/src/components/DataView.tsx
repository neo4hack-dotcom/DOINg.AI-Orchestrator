import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BarChart3, Bot, ChevronRight, Database, Download, FileSpreadsheet, History, Loader2, MessageSquare, Pencil, Play, RefreshCw, Send, Sigma,
  Table2, Trash2, User, X,
} from 'lucide-react';
import type { DataPage, DataProfile, DataSource, DataView as View, DatasetMeta, Message } from '../types';
import { api } from '../api';
import { cx, fmtDuration } from '../util';
import { DataGrid } from './DataGrid';
import { Markdown } from './Markdown';

type Hist = { ts: number; source: string; sql: string; ok: boolean; rows: number; by: string };

function Chart({ sessionId, page }: { sessionId: string; page: DataPage }) {
  const numeric = page.columns.filter((_, i) => ['integer', 'number'].includes(page.types[i]));
  const [x, setX] = useState(page.columns.find((_, i) => !['integer', 'number'].includes(page.types[i])) || page.columns[0] || '');
  const [y, setY] = useState(numeric[0] || '');
  const [agg, setAgg] = useState(numeric.length ? 'sum' : 'count');
  const [kind, setKind] = useState<'bar' | 'line'>('bar');
  const [points, setPoints] = useState<{ x: string; y: number }[]>([]);
  const [groups, setGroups] = useState(0);
  useEffect(() => {
    if (!x) return;
    api.dataAggregate(sessionId, page.dataset.id, x, agg === 'count' ? '' : y, agg).then((r) => { setPoints(r.points); setGroups(r.groups); }).catch(() => { setPoints([]); setGroups(0); });
  }, [sessionId, page.dataset.id, x, y, agg, JSON.stringify(page.dataset.view)]); // eslint-disable-line react-hooks/exhaustive-deps
  const max = Math.max(1, ...points.map((p) => Math.abs(p.y)));
  const W = 760, H = 220, pad = 30;
  const step = points.length ? (W - pad) / points.length : 0;
  return (
    <div className="border-b border-line px-3 py-2 text-[11px]">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="text-faint">x</span>
        <select value={x} onChange={(e) => setX(e.target.value)} className="rounded border border-line bg-bg px-1">{page.columns.map((c) => <option key={c}>{c}</option>)}</select>
        <select value={agg} onChange={(e) => setAgg(e.target.value)} className="rounded border border-line bg-bg px-1">
          {['count', 'sum', 'avg', 'min', 'max'].map((a) => <option key={a}>{a}</option>)}
        </select>
        {agg !== 'count' && (
          <select value={y} onChange={(e) => setY(e.target.value)} className="rounded border border-line bg-bg px-1">{numeric.map((c) => <option key={c}>{c}</option>)}</select>
        )}
        <select value={kind} onChange={(e) => setKind(e.target.value as 'bar' | 'line')} className="rounded border border-line bg-bg px-1">
          <option value="bar">bars</option><option value="line">line</option>
        </select>
        <span className="text-faint">{groups > points.length ? `top ${points.length} of ${groups} groups` : `${groups} groups`}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H + 40}`} className="h-56 w-full">
        <line x1={pad} y1={H} x2={W} y2={H} stroke="var(--line)" />
        {kind === 'bar' ? points.map((p, i) => {
          const h = (Math.abs(p.y) / max) * (H - 10);
          return (
            <g key={i}>
              <rect x={pad + i * step + 2} y={H - h} width={Math.max(2, step - 4)} height={h} fill="var(--accent)" opacity="0.8"><title>{`${p.x}: ${p.y}`}</title></rect>
              {points.length <= 24 && <text x={pad + i * step + step / 2} y={H + 12} fontSize="9" textAnchor="middle" fill="var(--muted)">{p.x.slice(0, 10)}</text>}
            </g>
          );
        }) : (
          <polyline fill="none" stroke="var(--accent)" strokeWidth="2"
            points={points.map((p, i) => `${pad + i * step + step / 2},${H - (Math.abs(p.y) / max) * (H - 10)}`).join(' ')} />
        )}
        <text x={2} y={12} fontSize="9" fill="var(--muted)">{Math.round(max * 100) / 100}</text>
      </svg>
    </div>
  );
}

function Profile({ profile }: { profile: DataProfile }) {
  return (
    <div className="max-h-60 overflow-y-auto border-b border-line px-3 py-2 text-[11px]">
      <div className="mb-1 text-faint">{profile.rows.toLocaleString()} rows in the view · {profile.duplicates} duplicate row(s)</div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {profile.columns.map((c) => (
          <div key={c.column} className="rounded border border-line bg-panel px-2 py-1">
            <div className="flex items-center gap-1"><span className="font-semibold">{c.column}</span><span className="text-faint">{c.type}</span>
              {c.nulls > 0 && <span className="ml-auto text-warn">{c.nulls} empty</span>}</div>
            <div className="text-muted">{c.distinct.toLocaleString()} distinct
              {c.mean !== undefined ? ` · min ${c.min} · max ${c.max} · mean ${c.mean} · median ${c.median}` : c.min !== undefined ? ` · ${String(c.min).slice(0, 16)} … ${String(c.max).slice(0, 16)}` : ''}</div>
            {c.top.length > 0 && <div className="truncate text-faint">top: {c.top.slice(0, 3).map(([v, n]) => `${v.slice(0, 18)} ×${n}`).join(', ')}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

/* Data analysis workspace: sources (database MCP servers, project data files), SQL editor, datasets shared with
   the agent, grid with filters / sort / profile / chart, CSV / Excel export, and a chat strip to ask the agent. */
export function DataView({ sessionId, messages, busy, signal, initialFile, onSend, onClose, onOpenSettings }: {
  sessionId: string;
  messages: Message[];
  busy: boolean;
  signal: { kind: 'dataset' | 'view'; id: string; nonce: number } | null;
  initialFile?: string | null;
  onSend: (text: string) => void;
  onClose: () => void;
  onOpenSettings: () => void;
}) {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [datasets, setDatasets] = useState<DatasetMeta[]>([]);
  const [history, setHistory] = useState<Hist[]>([]);
  const [mode, setMode] = useState('ask');
  const [loadingSources, setLoadingSources] = useState(true);
  const [tables, setTables] = useState<Record<string, { name: string }[] | 'loading' | string>>({});
  const [source, setSource] = useState('');
  const [sql, setSql] = useState('');
  const [page, setPage] = useState<DataPage | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<'none' | 'profile' | 'chart'>('none');
  const [profile, setProfile] = useState<DataProfile | null>(null);
  const [ask, setAsk] = useState('');
  const [answerOpen, setAnswerOpen] = useState(true);
  const editor = useRef<HTMLTextAreaElement>(null);

  const refresh = useCallback(() => {
    setLoadingSources(true);
    api.dataSources(sessionId).then((r) => {
      setSources(r.sources);
      setDatasets(r.datasets);
      setHistory(r.history);
      setMode(r.mode);
      setSource((s) => s || r.sources.find((x) => !x.error)?.id || '');
    }).catch((e) => setError(e.message)).finally(() => setLoadingSources(false));
  }, [sessionId]);
  useEffect(refresh, [refresh]);

  const openPage = useCallback((dsId: string, offset = 0) => {
    api.dataPage(sessionId, dsId, offset).then((p) => { setPage(p); setError(null); }).catch((e) => setError(e.message));
  }, [sessionId]);

  useEffect(() => {
    if (!initialFile) return;
    api.dataOpenFile(sessionId, initialFile).then((p) => { setPage(p); refresh(); }).catch((e) => setError(e.message));
  }, [initialFile, sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // The agent created a dataset or changed the view: follow it.
  useEffect(() => {
    if (!signal) return;
    if (signal.kind === 'dataset') {
      refresh();
      openPage(signal.id);
    } else if (page && signal.id === page.dataset.id) {
      openPage(signal.id, 0);
    }
  }, [signal?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (panel !== 'profile' || !page) return;
    api.dataProfile(sessionId, page.dataset.id).then(setProfile).catch(() => setProfile(null));
  }, [panel, page?.dataset.id, JSON.stringify(page?.dataset.view)]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (confirm = false) => {
    if (!source || !sql.trim()) return;
    setRunning(true);
    setError(null);
    try {
      const p = await api.dataQuery(sessionId, source, sql, confirm);
      setPage(p);
      refresh();
    } catch (e: any) {
      if (e.status === 409 && e.detail?.needsConfirmation) {
        if (window.confirm(`This statement changes data:\n\n${sql.slice(0, 600)}\n\nRun it on ${source}?`)) {
          setRunning(false);
          return run(true);
        }
      } else setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  const loadTables = async (src: DataSource) => {
    if (tables[src.id] && tables[src.id] !== 'loading' && typeof tables[src.id] !== 'string') {
      setTables((t) => { const n = { ...t }; delete n[src.id]; return n; });
      return;
    }
    setTables((t) => ({ ...t, [src.id]: 'loading' }));
    try {
      const list = await api.dataTables(sessionId, src.id);
      setTables((t) => ({ ...t, [src.id]: list }));
    } catch (e: any) {
      setTables((t) => ({ ...t, [src.id]: e.message }));
    }
  };

  const openTable = async (src: DataSource, table: string) => {
    setSource(src.id);
    setRunning(true);
    setError(null);
    try {
      const p = await api.dataOpenTable(sessionId, src.id, table);
      setPage(p);
      setSql(p.dataset.sql);
      refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  const setView = async (view: View) => {
    if (!page) return;
    try {
      setPage(await api.dataSetView(sessionId, page.dataset.id, view));
    } catch (e: any) {
      setError(e.message);
    }
  };

  const context = useMemo(() => {
    if (!page) return '';
    const v = page.dataset.view;
    const bits = [`dataset ${page.dataset.id} "${page.dataset.title}" from ${page.dataset.source}`, `${page.filtered} of ${page.total} rows shown`];
    if (v.filters.length) bits.push(`filters ${v.filters.map((f) => `${f.column} ${f.op} ${f.value ?? ''}`).join('; ')}`);
    if (v.sort.length) bits.push(`sorted by ${v.sort.map((s) => `${s.column}${s.desc ? ' desc' : ''}`).join(', ')}`);
    return `[Data view: ${bits.join(', ')}]`;
  }, [page]);

  const sendAsk = () => {
    const text = ask.trim();
    if (!text) return;
    onSend(`${context ? `${context}\n` : ''}${text}`);
    setAsk('');
    setAnswerOpen(true);
  };

  const lastAnswer = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'user') break;
      const t = m.parts.filter((p) => p.type === 'text' && !p.hidden).map((p) => (p as { text: string }).text).join('\n');
      if (t.trim()) return t;
    }
    return '';
  }, [messages]);

  const exportAs = (format: 'csv' | 'xlsx' | 'json', scope: 'view' | 'all') => {
    if (!page) return;
    const a = document.createElement('a');
    a.href = api.dataExportUrl(sessionId, page.dataset.id, format, scope);
    a.click();
  };

  const sourceIcon = (s: DataSource) => (s.kind === 'file' ? <FileSpreadsheet size={12} className="shrink-0 text-ok" /> : <Database size={12} className={cx('shrink-0', s.kind === 'mcp' ? 'text-accent' : 'text-warn')} />);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-bg" role="dialog" aria-modal aria-label="Data analysis">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <Database size={15} className="text-accent" />
        <span className="font-bold">Data</span>
        <span className="text-[11px] text-faint">query, explore and export - the agent sees and drives the same tables</span>
        <span className={cx('ml-2 rounded px-1.5 text-[10px]', mode === 'auto' ? 'bg-warn/15 text-warn' : 'bg-elev text-faint')}>
          {mode === 'auto' ? 'auto: writes run without confirmation' : 'writes need confirmation'}
        </span>
        <button type="button" onClick={refresh} title="Refresh sources" className="ml-auto rounded p-1 text-faint hover:text-fg"><RefreshCw size={13} /></button>
        <button type="button" onClick={onClose} className="rounded p-1 text-faint hover:text-fg" aria-label="Close"><X size={16} /></button>
      </div>
      <div className="flex min-h-0 flex-1">
        <aside className="flex w-64 shrink-0 flex-col overflow-y-auto border-r border-line text-[12px]">
          <div className="px-3 pb-1 pt-2 text-[10px] uppercase tracking-wider text-faint">Sources</div>
          {loadingSources && <div className="flex items-center gap-1 px-3 text-faint"><Loader2 size={11} className="spin" /> connecting…</div>}
          {!loadingSources && !sources.length && (
            <div className="space-y-1 px-3 text-[11px] text-faint">
              <p>No data source yet.</p>
              <p>Add a database MCP server (PostgreSQL, SQLite, MySQL, DuckDB…) in <button type="button" onClick={onOpenSettings} className="text-accent underline">Settings &gt; MCP</button>, or put a SQLite / CSV / XLSX file in the project.</p>
            </div>
          )}
          <ul className="px-1">
            {sources.map((s) => (
              <li key={s.id}>
                <button type="button" onClick={() => (s.kind === 'file' ? (setSource(s.id), api.dataOpenFile(sessionId, s.name).then((p) => { setPage(p); setSql(`SELECT * FROM ${s.table} LIMIT 100`); refresh(); }).catch((e) => setError(e.message))) : loadTables(s))}
                  className={cx('flex w-full min-w-0 items-center gap-1 rounded px-2 py-1 text-left hover:bg-elev', source === s.id && 'bg-accent/10')} title={s.error || s.id}>
                  {s.kind !== 'file' && <ChevronRight size={11} className={cx('shrink-0 text-faint transition-transform', Array.isArray(tables[s.id]) && 'rotate-90')} />}
                  {sourceIcon(s)}
                  <span className="truncate">{s.name}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-faint">{s.error ? 'offline' : s.dialect}</span>
                </button>
                {tables[s.id] === 'loading' && <div className="pl-8 text-[11px] text-faint">loading…</div>}
                {typeof tables[s.id] === 'string' && tables[s.id] !== 'loading' && <div className="pl-8 text-[11px] text-err">{tables[s.id] as string}</div>}
                {Array.isArray(tables[s.id]) && (
                  <ul className="pl-6">
                    {(tables[s.id] as { name: string }[]).map((t) => (
                      <li key={t.name}>
                        <button type="button" onClick={() => openTable(s, t.name)} className="flex w-full min-w-0 items-center gap-1 rounded px-2 py-0.5 text-left text-muted hover:bg-elev hover:text-fg">
                          <Table2 size={11} className="shrink-0 text-faint" /> <span className="truncate">{t.name}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          <div className="px-3 pb-1 pt-3 text-[10px] uppercase tracking-wider text-faint">Datasets</div>
          <ul className="px-1">
            {[...datasets].reverse().map((d) => (
              <li key={d.id} className={cx('group flex items-center gap-1 rounded px-2 py-0.5 hover:bg-elev', page?.dataset.id === d.id && 'bg-accent/10')}>
                {d.createdBy === 'agent' ? <Bot size={11} className="shrink-0 text-flow" /> : <User size={11} className="shrink-0 text-faint" />}
                <button type="button" onClick={() => openPage(d.id)} className="min-w-0 flex-1 truncate text-left" title={d.sql || d.title}>{d.title}</button>
                <span className="shrink-0 text-[10px] text-faint">{d.rowCount}</span>
                <button type="button" title="Rename" onClick={() => { const t = window.prompt('Dataset name', d.title); if (t) api.dataRename(sessionId, d.id, t).then(refresh); }}
                  className="hidden text-faint hover:text-fg group-hover:block"><Pencil size={10} /></button>
                <button type="button" title="Delete" onClick={() => api.dataDelete(sessionId, d.id).then(() => { if (page?.dataset.id === d.id) setPage(null); refresh(); })}
                  className="hidden text-faint hover:text-err group-hover:block"><Trash2 size={10} /></button>
              </li>
            ))}
            {!datasets.length && <li className="px-2 text-[11px] text-faint">Results of your queries and of the agent's appear here.</li>}
          </ul>
          {history.length > 0 && (
            <>
              <div className="flex items-center gap-1 px-3 pb-1 pt-3 text-[10px] uppercase tracking-wider text-faint"><History size={10} /> History</div>
              <ul className="px-1 pb-3">
                {[...history].reverse().slice(0, 20).map((h, i) => (
                  <li key={i}>
                    <button type="button" onClick={() => { setSource(h.source); setSql(h.sql); editor.current?.focus(); }} title={h.sql}
                      className="block w-full truncate rounded px-2 py-0.5 text-left font-mono text-[10.5px] text-muted hover:bg-elev hover:text-fg">
                      <span className={h.ok ? 'text-ok' : 'text-err'}>{h.by === 'agent' ? '◆' : '●'}</span> {h.sql.replace(/\s+/g, ' ')}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="border-b border-line p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px]">
              <select value={source} onChange={(e) => setSource(e.target.value)} className="max-w-[260px] rounded border border-line bg-bg px-1.5 py-0.5">
                <option value="">source…</option>
                {sources.filter((s) => !s.error).map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}
              </select>
              <span className="text-faint">Ctrl+Enter runs · reads need no confirmation</span>
              <button type="button" onClick={() => run()} disabled={running || !source || !sql.trim()}
                className="ml-auto inline-flex items-center gap-1 rounded bg-accent px-2.5 py-0.5 font-semibold text-bg disabled:opacity-40">
                {running ? <Loader2 size={12} className="spin" /> : <Play size={12} />} Run
              </button>
            </div>
            <textarea ref={editor} value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false}
              onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); run(); } }}
              placeholder="SELECT * FROM orders WHERE country = 'France' ORDER BY amount DESC LIMIT 100"
              className="h-24 w-full resize-y rounded border border-line bg-panel px-2 py-1.5 font-mono text-[12px] leading-[1.5] outline-none focus:border-accent" />
          </div>
          {error && <div className="border-b border-err/30 bg-err/5 px-3 py-1 text-[12px] text-err">{error}</div>}
          {page ? (
            <>
              <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-1.5 text-[11px]">
                <span className="font-semibold text-fg">{page.dataset.title}</span>
                <span className="text-faint">{page.dataset.source} · {fmtDuration(page.dataset.elapsedMs || 0)}{page.dataset.affected ? ` · ${page.dataset.affected} row(s) affected` : ''}</span>
                <span className="ml-auto flex items-center gap-1">
                  <button type="button" onClick={() => setPanel(panel === 'profile' ? 'none' : 'profile')}
                    className={cx('inline-flex items-center gap-1 rounded border px-1.5 py-0.5', panel === 'profile' ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}>
                    <Sigma size={11} /> Profile
                  </button>
                  <button type="button" onClick={() => setPanel(panel === 'chart' ? 'none' : 'chart')}
                    className={cx('inline-flex items-center gap-1 rounded border px-1.5 py-0.5', panel === 'chart' ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}>
                    <BarChart3 size={11} /> Chart
                  </button>
                  {(['csv', 'xlsx', 'json'] as const).map((f) => (
                    <button key={f} type="button" onClick={(e) => exportAs(f, e.shiftKey ? 'all' : 'view')} title="Export the current view (Shift: all rows)"
                      className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-muted hover:text-fg">
                      <Download size={11} /> {f === 'xlsx' ? 'Excel' : f.toUpperCase()}
                    </button>
                  ))}
                  <button type="button" onClick={() => setAsk((a) => a || 'Check this data: ')} title="Ask the agent about this data"
                    className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-muted hover:text-fg"><MessageSquare size={11} /> Ask</button>
                </span>
              </div>
              {panel === 'profile' && profile && <Profile profile={profile} />}
              {panel === 'chart' && <Chart key={page.dataset.id} sessionId={sessionId} page={page} />}
              <DataGrid page={page} onView={setView} onPage={(o) => openPage(page.dataset.id, o)} busy={running} />
            </>
          ) : (
            <div className="grid flex-1 place-items-center p-6 text-center text-[12px] text-faint">
              <div className="max-w-md space-y-2">
                <Database size={28} className="mx-auto text-accent/60" />
                <p>Pick a table on the left, write SQL above, or ask the agent below - for example
                  <i> "top 10 customers by revenue this year"</i> or <i>"check orders for duplicates and missing emails"</i>.</p>
                <p>Filter, sort and pick columns in the grid; the agent sees the same view and can change it for you.</p>
              </div>
            </div>
          )}
          <div className="border-t border-line bg-panel">
            {lastAnswer && answerOpen && (
              <div className="relative max-h-48 overflow-y-auto border-b border-line px-4 py-2 text-[12px]">
                <button type="button" onClick={() => setAnswerOpen(false)} className="absolute right-2 top-1 text-faint hover:text-fg" aria-label="Hide"><X size={12} /></button>
                <Markdown text={lastAnswer} />
              </div>
            )}
            <div className="flex items-center gap-2 px-3 py-2">
              <Bot size={14} className={cx('shrink-0', busy ? 'pulse text-accent' : 'text-faint')} />
              <input value={ask} onChange={(e) => setAsk(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && sendAsk()}
                placeholder={busy ? 'The agent is working… (your message will be queued)' : 'Ask the agent: write SQL, check the data, filter / sort the table, export…'}
                className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-faint" />
              {context && <span className="hidden max-w-[260px] truncate text-[10px] text-faint lg:inline" title={context}>{context}</span>}
              <button type="button" onClick={sendAsk} disabled={!ask.trim()} className="rounded bg-accent p-1.5 text-bg disabled:opacity-30" aria-label="Send"><Send size={12} /></button>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
