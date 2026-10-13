import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, Brain, CheckCircle2, ClipboardList, Download, FileCode2, FileText, Filter, GitPullRequest, ListChecks, Loader2,
  MessageSquare, Plug, RefreshCw, Search, ShieldAlert, Sparkles, Wrench, X, XCircle,
} from 'lucide-react';
import type { Activity, ActivityItem } from '../types';
import { api } from '../api';
import { cx, fmtDuration, fmtTokens } from '../util';
import { Markdown } from './Markdown';
import { ReasoningBlock } from './ReasoningBlock';
import { TierChip } from './TierIcon';

type Tab = 'overview' | 'timeline' | 'reasoning' | 'plan' | 'validation' | 'actions' | 'files' | 'extensions';
type Kind = 'all' | 'request' | 'reasoning' | 'tool' | 'plan' | 'validation' | 'event' | 'error';

const time = (ts?: number | null) => (ts ? new Date(ts).toLocaleTimeString() : '');
const dateTime = (ts?: number | null) => (ts ? new Date(ts).toLocaleString() : '');

function Kpi({ value, label, tone }: { value: string; label: string; tone?: 'ok' | 'err' }) {
  return (
    <div className="rounded-md border border-line bg-panel px-3 py-2">
      <div className={cx('text-[18px] font-bold tabular-nums', tone === 'ok' && 'text-ok', tone === 'err' && 'text-err')}>{value}</div>
      <div className="text-[11px] text-faint">{label}</div>
    </div>
  );
}

function Pass({ ok, label }: { ok: boolean | null | undefined; label?: string }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 rounded bg-ok/15 px-1.5 text-[11px] font-semibold text-ok"><CheckCircle2 size={11} /> {label || 'PASS'}</span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded bg-err/15 px-1.5 text-[11px] font-semibold text-err"><XCircle size={11} /> {label || 'FAIL'}</span>
  );
}

const KIND_ICON: Record<string, ReactNode> = {
  request: <MessageSquare size={13} className="text-build" />,
  reasoning: <Brain size={13} className="text-accent" />,
  answer: <FileText size={13} className="text-muted" />,
  tool: <Wrench size={13} className="text-muted" />,
  review: <ListChecks size={13} className="text-flow" />,
  plan: <ClipboardList size={13} className="text-flow" />,
  error: <AlertTriangle size={13} className="text-err" />,
  notice: <Sparkles size={13} className="text-faint" />,
  compaction: <Sparkles size={13} className="text-faint" />,
  event: <Sparkles size={13} className="text-faint" />,
};

function itemTitle(it: ActivityItem): string {
  switch (it.kind) {
    case 'request': return it.command ? `/${it.command}` : 'Request';
    case 'reasoning': return 'Reasoning';
    case 'answer': return 'Answer';
    case 'tool': return `${it.tool}${it.mcp ? ` (MCP ${it.mcp.server})` : ''}`;
    case 'review': return `Review task ${it.taskN} · ${it.verdict}`;
    case 'plan': return `Plan · ${it.title} (${it.tasks} tasks${it.modules ? `, ${it.modules} modules` : ''})`;
    case 'event': return it.event;
    default: return it.kind;
  }
}

function itemText(it: ActivityItem): string {
  if (it.kind === 'tool') return `${it.target || ''}${it.output ? `\n\n${it.output}` : ''}`;
  if (it.kind === 'review') return [...(it.defects || []).map((d: string) => `- ${d}`), it.note].filter(Boolean).join('\n');
  if (it.kind === 'plan') return it.summary || '';
  return it.text || '';
}

function matchesKind(it: ActivityItem, k: Kind): boolean {
  if (k === 'all') return true;
  if (k === 'request') return it.kind === 'request' || it.kind === 'answer';
  if (k === 'reasoning') return it.kind === 'reasoning';
  if (k === 'tool') return it.kind === 'tool';
  if (k === 'plan') return it.kind === 'plan' || it.kind === 'review' || (it.kind === 'event' && ['phase', 'plan', 'split', 'fix', 'flow_end'].includes(it.event));
  if (k === 'validation') return it.kind === 'tool' && it.tool === 'bash' && it.exitCode !== undefined && it.exitCode !== null;
  if (k === 'event') return it.kind === 'event' || it.kind === 'notice' || it.kind === 'compaction';
  if (k === 'error') return it.kind === 'error' || (it.kind === 'tool' && it.status === 'error') || (it.kind === 'event' && ['retry', 'loop', 'abort'].includes(it.event));
  return true;
}

function TimelineItem({ it }: { it: ActivityItem }) {
  const [open, setOpen] = useState(false);
  const body = itemText(it);
  const failed = it.kind === 'error' || (it.kind === 'tool' && it.status === 'error');
  return (
    <li className="relative pl-6">
      <span className="absolute left-0 top-1">{KIND_ICON[it.kind] || KIND_ICON.event}</span>
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full min-w-0 items-baseline gap-2 text-left">
        <span className="shrink-0 tabular-nums text-[11px] text-faint">{time(it.ts)}</span>
        <span className={cx('shrink-0 font-semibold', failed ? 'text-err' : 'text-fg')}>{itemTitle(it)}</span>
        {it.agent && <span className="shrink-0 text-[11px] text-faint">{it.agent}{it.runTitle ? ` · ${it.runTitle}` : ''}</span>}
        {it.kind === 'tool' && it.durationMs !== null && it.durationMs !== undefined && <span className="shrink-0 text-[11px] text-faint">{fmtDuration(it.durationMs)}</span>}
        {it.blockedBy && <span className="shrink-0 rounded bg-err/15 px-1 text-[10px] text-err">blocked · {it.blockedBy}</span>}
        <span className="min-w-0 truncate text-[12px] text-muted">{it.kind === 'tool' ? it.target : body.replace(/\s+/g, ' ').slice(0, 160)}</span>
      </button>
      {open && body && (
        it.kind === 'request' || it.kind === 'answer' || it.kind === 'plan'
          ? <div className="mt-1 rounded border border-line bg-panel px-3 py-2 text-[12px]"><Markdown text={body} /></div>
          : <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded border border-line bg-panel px-3 py-2 text-[11.5px] text-muted">{body}</pre>
      )}
    </li>
  );
}

/* Everything the session did - reasoning, savvy plan, tests, actions, files, extensions - with PDF / Markdown export. */
export function ActivityView({ sessionId, busy, onClose }: { sessionId: string; busy: boolean; onClose: () => void }) {
  const [data, setData] = useState<Activity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [kind, setKind] = useState<Kind>('all');
  const [query, setQuery] = useState('');
  const [reasoningMode, setReasoningMode] = useState<'summary' | 'full' | 'none'>('summary');
  const [withActions, setWithActions] = useState(true);
  const [exporting, setExporting] = useState(false);

  const load = useCallback(() => {
    api.activity(sessionId).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [sessionId]);
  useEffect(load, [load]);
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(load, 6000);
    return () => clearInterval(id);
  }, [busy, load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const download = async (format: 'pdf' | 'md') => {
    setExporting(true);
    try {
      const res = await fetch(api.reportUrl(sessionId, format, reasoningMode, withActions));
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      const blob = await res.blob();
      const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') || '')?.[1] || `supercode-report.${format}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setExporting(false);
    }
  };

  const timeline = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.timeline.filter((it) => matchesKind(it, kind) && (!q || `${itemTitle(it)} ${itemText(it)} ${it.agent || ''}`.toLowerCase().includes(q)));
  }, [data, kind, query]);

  const o = data?.overview;
  const tabs: [Tab, string, number | null][] = data ? [
    ['overview', 'Overview', null],
    ['timeline', 'Timeline', data.timeline.length],
    ['reasoning', 'Reasoning', data.reasoning.length],
    ['plan', 'Savvy plan', data.plan ? data.plan.tasks.length : null],
    ['validation', 'Validation', data.validation.length + data.reviews.length],
    ['actions', 'Actions', data.tools.length],
    ['files', 'Files', data.files.length],
    ['extensions', 'Extensions', null],
  ] : [];

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-2 sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal aria-label="Session activity" className="flex h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-lg border border-line bg-bg shadow-2xl">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
          <ClipboardList size={15} className="text-accent" />
          <span className="font-bold">Activity</span>
          <span className="min-w-0 flex-1 truncate text-[12px] text-faint">{o?.title} · {o?.directory}</span>
          <button type="button" onClick={load} title="Refresh" className="rounded p-1.5 text-faint hover:bg-elev hover:text-fg"><RefreshCw size={13} /></button>
          <div className="flex items-center gap-1.5 rounded border border-line px-2 py-1 text-[11px] text-muted">
            <span>Reasoning in export</span>
            <select value={reasoningMode} onChange={(e) => setReasoningMode(e.target.value as typeof reasoningMode)} className="bg-transparent text-fg outline-none">
              <option value="summary">summary</option>
              <option value="full">full</option>
              <option value="none">none</option>
            </select>
            <label className="ml-1 flex items-center gap-1"><input type="checkbox" checked={withActions} onChange={(e) => setWithActions(e.target.checked)} className="accent-[var(--accent)]" /> action log</label>
          </div>
          <button type="button" onClick={() => download('md')} disabled={exporting} className="inline-flex items-center gap-1 rounded border border-line px-2.5 py-1 text-[12px] text-muted hover:text-fg disabled:opacity-50">
            <FileCode2 size={13} /> Markdown
          </button>
          <button type="button" onClick={() => download('pdf')} disabled={exporting} className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg hover:brightness-110 disabled:opacity-50">
            {exporting ? <Loader2 size={13} className="spin" /> : <Download size={13} />} Export PDF
          </button>
          <button type="button" onClick={onClose} className="rounded p-1 text-faint hover:text-fg" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="flex gap-1 overflow-x-auto border-b border-line px-3" role="tablist">
          {tabs.map(([k, label, n]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
              className={cx('flex shrink-0 items-center gap-1 border-b-2 px-2 py-2 text-[12px]', tab === k ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg')}>
              {label}{n ? <span className="rounded bg-elev px-1 text-[10px] text-faint">{n}</span> : null}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {error && <div className="mb-3 rounded border border-err/40 bg-err/5 px-3 py-1.5 text-[12px] text-err">{error}</div>}
          {!data ? (
            <div className="flex items-center gap-2 text-muted"><Loader2 size={14} className="spin" /> Loading the activity…</div>
          ) : (
            <>
              {tab === 'overview' && o && (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
                    <Kpi value={fmtDuration(o.durationMs)} label="session span" />
                    <Kpi value={String(o.requests)} label="requests" />
                    <Kpi value={String(o.reasoningSteps)} label="reasoning steps" />
                    <Kpi value={String(o.toolCalls)} label={`actions (${o.toolErrors} failed)`} />
                    <Kpi value={String(o.filesChanged)} label="files changed" />
                    <Kpi value={`${o.checksPassed}/${o.checks}`} label="checks passed" tone={o.checks ? (o.checksPassed === o.checks ? 'ok' : 'err') : undefined} />
                    <Kpi value={fmtTokens(o.tokensIn + o.tokensOut)} label="tokens" />
                  </div>
                  <div className="grid gap-x-6 gap-y-1 rounded-md border border-line bg-panel px-3 py-2 text-[12px] sm:grid-cols-2">
                    <div><span className="text-faint">Model</span> {o.model} ({o.provider}){o.orchestratorModel !== o.model ? ` · orchestrator ${o.orchestratorModel}` : ''}</div>
                    <div><span className="text-faint">Mode</span> {o.autoMode ? 'auto (no approvals)' : 'supervised'} · agent {o.agent}</div>
                    <div><span className="text-faint">Started</span> {dateTime(o.createdAt)}</div>
                    <div><span className="text-faint">Last activity</span> {dateTime(o.updatedAt)}</div>
                    <div><span className="text-faint">Workers</span> {o.workers} · <span className="text-faint">reviews</span> {o.reviews} · <span className="text-faint">compactions</span> {o.compactions}</div>
                    <div><span className="text-faint">Tokens</span> {o.tokensIn.toLocaleString()} in / {o.tokensOut.toLocaleString()} out</div>
                  </div>
                  {data.plan && (
                    <div className="rounded-md border border-flow/40 bg-flow/5 px-3 py-2">
                      <div className="flex items-center gap-2 text-[12px]">
                        <ClipboardList size={13} className="text-flow" />
                        <span className="font-semibold">{data.plan.title}</span>
                        <span className="text-faint">{data.plan.done}/{data.plan.total} tasks accepted · phase {data.plan.phase}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
                        <div className="h-full bg-flow" style={{ width: `${data.plan.total ? (100 * data.plan.done) / data.plan.total : 0}%` }} />
                      </div>
                      {data.plan.summary && <div className="mt-1.5 text-[12px] text-muted">{data.plan.summary}</div>}
                    </div>
                  )}
                  <div>
                    <div className="mb-1 text-[12px] font-semibold text-muted">Requests</div>
                    <ul className="space-y-1">
                      {data.requests.map((r, i) => (
                        <li key={i} className="rounded border-l-2 border-build bg-panel px-3 py-1.5 text-[12px]">
                          <span className="mr-2 text-[11px] text-faint">{time(r.ts)}</span>{r.text}
                        </li>
                      ))}
                    </ul>
                  </div>
                  {o.lastAnswer && (
                    <div>
                      <div className="mb-1 text-[12px] font-semibold text-muted">Outcome (last answer)</div>
                      <div className="rounded-md border border-line bg-panel px-3 py-2 text-[12px]"><Markdown text={o.lastAnswer} /></div>
                    </div>
                  )}
                </div>
              )}

              {tab === 'timeline' && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Filter size={12} className="text-faint" />
                    {(['all', 'request', 'reasoning', 'tool', 'plan', 'validation', 'event', 'error'] as Kind[]).map((k) => (
                      <button key={k} type="button" onClick={() => setKind(k)}
                        className={cx('rounded border px-2 py-0.5 text-[11px]', kind === k ? 'border-accent bg-accent/15 text-accent' : 'border-line text-muted hover:text-fg')}>
                        {{ all: 'All', request: 'Requests & answers', reasoning: 'Reasoning', tool: 'Actions', plan: 'Plan & reviews', validation: 'Commands', event: 'Events', error: 'Errors' }[k]}
                      </button>
                    ))}
                    <span className="ml-auto flex items-center gap-1 rounded border border-line px-2 py-0.5">
                      <Search size={11} className="text-faint" />
                      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter…" className="w-40 bg-transparent text-[12px] outline-none" />
                    </span>
                  </div>
                  <ol className="space-y-1.5">{timeline.map((it, i) => <TimelineItem key={`${it.ts}-${i}`} it={it} />)}</ol>
                  {!timeline.length && <div className="text-[12px] text-faint">Nothing matches.</div>}
                </div>
              )}

              {tab === 'reasoning' && (
                <div className="space-y-3">
                  {!data.reasoning.length && <div className="text-[12px] text-faint">No reasoning streamed yet (the model may not expose its thinking).</div>}
                  {data.reasoning.map((r, i) => (
                    <div key={i} className="rounded-md border border-line bg-panel px-3 py-2">
                      <div className="mb-1 flex items-center gap-2 text-[11px] text-faint">
                        <span className="font-semibold text-accent">Step {i + 1}</span>
                        <span>{r.agent}{r.runTitle ? ` · ${r.runTitle}` : ''}</span>
                        <span>{dateTime(r.ts)}</span>
                      </div>
                      <ReasoningBlock text={r.text || ''} live={false} startedAt={r.ts} endedAt={r.durationMs ? r.ts + r.durationMs : r.ts} />
                    </div>
                  ))}
                </div>
              )}

              {tab === 'plan' && (
                data.plan ? (
                  <div className="space-y-4">
                    <div>
                      <div className="font-semibold">{data.plan.title}</div>
                      <div className="text-[12px] text-muted">{data.plan.summary}</div>
                      <div className="mt-1 text-[11px] text-faint">mode {data.plan.mode} · phase {data.plan.phase} · {data.plan.done}/{data.plan.total} accepted · {fmtTokens(data.plan.tokens)} flow tokens</div>
                    </div>
                    {data.plan.modules.length > 0 && (
                      <div className="grid gap-2 sm:grid-cols-2">
                        {data.plan.modules.map((m) => (
                          <div key={m.id} className="rounded border border-line bg-panel px-3 py-2 text-[12px]">
                            <div className="font-semibold">{m.id} <span className="font-normal text-muted">{m.name}</span></div>
                            <div className="text-muted">{m.goal}</div>
                            {m.dependsOn?.length > 0 && <div className="text-[11px] text-faint">after {m.dependsOn.join(', ')}</div>}
                          </div>
                        ))}
                      </div>
                    )}
                    <table className="w-full text-left text-[12px]">
                      <thead className="text-[11px] text-faint"><tr><th className="py-1">#</th><th>Task</th><th>Module</th><th>Tier</th><th>Size</th><th>Status</th><th>Rounds</th><th>Duration</th></tr></thead>
                      <tbody>
                        {data.plan.tasks.map((t) => (
                          <tr key={t.n} className="border-t border-line align-top">
                            <td className="py-1.5 pr-2 text-faint">{t.n}</td>
                            <td className="pr-2">
                              <div className="font-semibold">{t.title}</div>
                              {t.files.length > 0 && <div className="text-[11px] text-faint">{t.files.join(', ')}</div>}
                              {t.defects.map((d, i) => <div key={i} className="text-[11px] text-err">- {d}</div>)}
                              {t.note && <div className="text-[11px] text-muted">{t.note}</div>}
                            </td>
                            <td className="pr-2 text-muted">{t.module}</td>
                            <td className="pr-2"><TierChip tier={t.tier} /></td>
                            <td className="pr-2 text-muted">{t.size}{t.complexity ? `/${t.complexity}` : ''}</td>
                            <td className={cx('pr-2', t.status === 'accepted' ? 'text-ok' : t.status === 'failed' || t.status === 'blocked' ? 'text-err' : 'text-muted')}>{t.status}</td>
                            <td className="pr-2 text-muted">{t.rounds}</td>
                            <td className="text-muted">{fmtDuration(t.durationMs)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {data.workers.length > 0 && (
                      <div>
                        <div className="mb-1 text-[12px] font-semibold text-muted">Worker reports</div>
                        <div className="space-y-2">
                          {data.workers.map((w) => (
                            <details key={w.id} className="rounded border border-line bg-panel px-3 py-1.5 text-[12px]">
                              <summary className="cursor-pointer">
                                <TierChip tier={w.tier} /> <span className="font-semibold">{w.description}</span>
                                <span className="ml-2 text-[11px] text-faint">{w.status} · {w.steps} steps · {fmtTokens(w.tokens)} tok{w.endedAt ? ` · ${fmtDuration(w.endedAt - w.startedAt)}` : ''}</span>
                              </summary>
                              <div className="mt-1.5"><Markdown text={w.report || '(no report)'} /></div>
                            </details>
                          ))}
                        </div>
                      </div>
                    )}
                    {data.plan.spec && <details className="rounded border border-line bg-panel px-3 py-1.5 text-[12px]"><summary className="cursor-pointer font-semibold">Specification</summary><Markdown text={data.plan.spec} /></details>}
                    {data.plan.design && <details className="rounded border border-line bg-panel px-3 py-1.5 text-[12px]"><summary className="cursor-pointer font-semibold">Design decisions</summary><Markdown text={data.plan.design} /></details>}
                  </div>
                ) : <div className="text-[12px] text-faint">No savvy-flow ran in this session. Start one with /flow &lt;task&gt;.</div>
              )}

              {tab === 'validation' && (
                <div className="space-y-4">
                  <table className="w-full text-left text-[12px]">
                    <thead className="text-[11px] text-faint"><tr><th className="py-1">Time</th><th>Source</th><th>Command</th><th>Result</th></tr></thead>
                    <tbody>
                      {data.validation.map((v, i) => (
                        <tr key={i} className="border-t border-line align-top">
                          <td className="whitespace-nowrap py-1.5 pr-2 text-faint">{time(v.ts)}</td>
                          <td className="pr-2 text-muted">{v.source}</td>
                          <td className="pr-2"><code className="break-all text-[11.5px]">{v.command}</code></td>
                          <td className="whitespace-nowrap"><Pass ok={v.ok} label={`${v.ok ? 'PASS' : 'FAIL'} · exit ${v.exitCode ?? '?'}`} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!data.validation.length && <div className="text-[12px] text-faint">No test, build, lint or verify command ran yet.</div>}
                  {data.reviews.length > 0 && (
                    <div>
                      <div className="mb-1 text-[12px] font-semibold text-muted">Orchestrator reviews</div>
                      <ul className="space-y-1.5">
                        {data.reviews.map((r, i) => (
                          <li key={i} className="rounded border border-line bg-panel px-3 py-1.5 text-[12px]">
                            <Pass ok={r.verdict === 'accept'} label={r.verdict} /> <span className="font-semibold">Task {r.taskN}. {r.title}</span>
                            <span className="ml-2 text-[11px] text-faint">round {(r.round || 0) + 1}</span>
                            {(r.defects || []).map((d: string, k: number) => <div key={k} className="text-[11px] text-err">- {d}</div>)}
                            {r.note && <div className="text-[11px] text-muted">{r.note}</div>}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {tab === 'actions' && (
                <div className="space-y-4">
                  <div className="space-y-1">
                    {data.toolStats.map((s) => {
                      const max = Math.max(...data.toolStats.map((x) => x.calls));
                      return (
                        <div key={s.tool} className="flex items-center gap-2 text-[12px]">
                          <span className="w-36 shrink-0 truncate">{s.tool}</span>
                          <span className="h-2 flex-1 overflow-hidden rounded-full bg-line">
                            <span className="block h-full bg-accent/70" style={{ width: `${(100 * s.calls) / max}%` }} />
                          </span>
                          <span className="w-28 shrink-0 text-right tabular-nums text-faint">{s.calls} · {s.errors} err · {fmtDuration(s.ms)}</span>
                        </div>
                      );
                    })}
                  </div>
                  <ol className="space-y-1">{data.tools.map((it, i) => <TimelineItem key={i} it={it} />)}</ol>
                </div>
              )}

              {tab === 'files' && (
                <table className="w-full text-left text-[12px]">
                  <thead className="text-[11px] text-faint"><tr><th className="py-1">File</th><th>Added</th><th>Removed</th><th>Edits</th><th>By</th></tr></thead>
                  <tbody>
                    {data.files.map((f) => (
                      <tr key={f.path} className="border-t border-line">
                        <td className="py-1 pr-2"><code className="break-all">{f.path}</code></td>
                        <td className="pr-2 text-ok">+{f.additions}</td>
                        <td className="pr-2 text-err">-{f.deletions}</td>
                        <td className="pr-2 text-muted">{f.edits}</td>
                        <td className="text-[11px] text-faint">{f.agents.join(', ')}</td>
                      </tr>
                    ))}
                    {!data.files.length && <tr><td className="py-2 text-faint" colSpan={5}>No file changed by the agents.</td></tr>}
                  </tbody>
                </table>
              )}

              {tab === 'extensions' && (
                <div className="grid gap-3 lg:grid-cols-2">
                  <section className="rounded-md border border-line bg-panel px-3 py-2 text-[12px]">
                    <div className="mb-1 flex items-center gap-1.5 font-semibold"><Plug size={13} className="text-accent" /> MCP servers used</div>
                    {data.extensions.mcp.length ? data.extensions.mcp.map((m) => <div key={m.server}>{m.server} · {m.calls} call(s)</div>) : <div className="text-faint">none</div>}
                  </section>
                  <section className="rounded-md border border-line bg-panel px-3 py-2 text-[12px]">
                    <div className="mb-1 flex items-center gap-1.5 font-semibold"><Sparkles size={13} className="text-accent" /> Skills used</div>
                    {data.extensions.skills.length ? data.extensions.skills.join(', ') : <div className="text-faint">none</div>}
                  </section>
                  <section className="rounded-md border border-line bg-panel px-3 py-2 text-[12px]">
                    <div className="mb-1 flex items-center gap-1.5 font-semibold"><ShieldAlert size={13} className="text-warn" /> Actions blocked by mods</div>
                    {data.extensions.blocked.length ? data.extensions.blocked.map((b, i) => <div key={i} className="truncate">{time(b.ts)} {b.tool} <code>{b.target}</code> · {b.blockedBy}</div>) : <div className="text-faint">none</div>}
                  </section>
                  <section className="rounded-md border border-line bg-panel px-3 py-2 text-[12px]">
                    <div className="mb-1 font-semibold">Mod events</div>
                    {data.extensions.mods.length ? data.extensions.mods.slice(-30).map((m, i) => <div key={i} className="truncate">{time(m.ts)} {m.mod} · {m.event} {m.summary || m.why || m.message || ''}</div>) : <div className="text-faint">none</div>}
                  </section>
                  <section className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] lg:col-span-2">
                    <div className="mb-1 font-semibold">Permission decisions</div>
                    {data.permissions.length ? data.permissions.map((p, i) => (
                      <div key={i} className="flex gap-2"><span className="text-faint">{time(p.createdAt)}</span><span>{p.agent}</span><span className="text-muted">{p.category}/{p.tool}</span>
                        <code className="min-w-0 truncate">{p.target}</code><span className={cx('ml-auto', p.status === 'allowed' ? 'text-ok' : 'text-err')}>{p.status}</span></div>
                    )) : <div className="text-faint">none</div>}
                  </section>
                  {data.questions.length > 0 && (
                    <section className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] lg:col-span-2">
                      <div className="mb-1 font-semibold">Questions and answers</div>
                      {data.questions.map((q, i) => <div key={i}><span className="text-faint">{time(q.createdAt)} {q.agent}</span> {q.questions.join(' / ')} → <span className="text-muted">{(q.answers || []).map((a) => a.join(', ')).join(' / ')}</span></div>)}
                    </section>
                  )}
                  {data.pullRequests.length > 0 && (
                    <section className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] lg:col-span-2">
                      <div className="mb-1 flex items-center gap-1.5 font-semibold"><GitPullRequest size={13} /> Pull requests</div>
                      {data.pullRequests.map((p) => <div key={p.url}><a className="text-accent underline" href={p.url} target="_blank" rel="noreferrer">{p.title || p.url}</a> {p.branch} → {p.target}</div>)}
                    </section>
                  )}
                  {data.issues.length > 0 && (
                    <section className="rounded-md border border-err/30 bg-err/5 px-3 py-2 text-[12px] lg:col-span-2">
                      <div className="mb-1 flex items-center gap-1.5 font-semibold text-err"><AlertTriangle size={13} /> Issues</div>
                      {data.issues.map((x, i) => <div key={i} className="truncate"><span className="text-faint">{time(x.ts)} {x.kind}</span> {x.text}</div>)}
                    </section>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
