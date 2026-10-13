import { useEffect, useMemo, useState } from 'react';
import { Download } from 'lucide-react';
import type { JournalEntry } from '../types';
import { api } from '../api';
import { useT } from '../i18n';
import { cx } from '../util';

type Filter = 'all' | 'tools' | 'decisions' | 'errors';
const DECISIONS = new Set(['user', 'plan', 'phase', 'task', 'verdict', 'verify', 'commit', 'fix', 'split', 'goal', 'goal_eval', 'approve', 'resume', 'flow', 'flow_end', 'note', 'question', 'permission', 'revert', 'fork', 'autonomous']);
const ERRORS = new Set(['error', 'retry', 'loop', 'abort']);

function describe(e: JournalEntry): string {
  switch (e.kind) {
    case 'tool': return `${e.agent} ${e.tool} ${e.target || ''}${e.exitCode !== undefined ? ` (exit ${e.exitCode})` : ''}`;
    case 'user': return `${e.agent}: ${e.text}`;
    case 'phase': return `→ ${e.phase}`;
    case 'task': return `#${e.n} ${e.title} → ${e.status}${e.tier ? ` (${e.tier})` : ''}`;
    case 'verdict': return `#${e.n} ${e.verdict}${e.auto ? ' (auto)' : ''}${e.defects?.length ? ` — ${e.defects.join('; ')}` : ''}`;
    case 'verify': return `${e.command} → exit ${e.exitCode}`;
    case 'plan': return `${e.title}: ${e.tasks} tasks, ${e.modules} modules (${e.mode})`;
    case 'commit': return e.message;
    case 'retry': return `${e.agent} retry ${e.attempt}: ${e.error}`;
    case 'compaction': return `${e.agent}: ${e.before} → ${e.after} tokens`;
    case 'loop': return `${e.agent}: repeated ${e.tool}`;
    case 'permission': return `${e.tool} ${e.target || ''} → ${e.status}`;
    case 'note': return `[${e.author}] ${e.text}`;
    case 'fix': return `${e.reason}: ${(e.tasks || []).join(', ')}`;
    case 'goal_eval': return `${e.status}: ${e.reason}`;
    default: {
      const { ts: _ts, kind: _k, ...rest } = e;
      return JSON.stringify(rest).slice(0, 200);
    }
  }
}

/* The session journal: every tool call, decision, verdict, check and error, live. */
export function TracePanel({ sessionId, live }: { sessionId: string | null; live: JournalEntry[] }) {
  const t = useT();
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [filter, setFilter] = useState<Filter>('decisions');

  useEffect(() => {
    if (!sessionId) return;
    api.journal(sessionId).then(setEntries).catch(() => setEntries([]));
  }, [sessionId]);

  const all = useMemo(() => {
    const seen = new Set(entries.map((e) => `${e.ts}${e.kind}`));
    return [...entries, ...live.filter((e) => !seen.has(`${e.ts}${e.kind}`))];
  }, [entries, live]);

  const shown = all
    .filter((e) => filter === 'all' || (filter === 'tools' ? e.kind === 'tool' : filter === 'errors' ? ERRORS.has(e.kind) || e.status === 'error' : DECISIONS.has(e.kind)))
    .slice(-400)
    .reverse();

  const download = async () => {
    if (!sessionId) return;
    const md = await api.report(sessionId);
    const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `report-${sessionId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const filters: [Filter, string][] = [['decisions', t.filterDecisions], ['tools', t.filterTools], ['errors', t.filterErrors], ['all', t.filterAll]];
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
        {filters.map(([k, label]) => (
          <button key={k} type="button" onClick={() => setFilter(k)} className={cx('rounded px-1.5 py-0.5 text-[11px]', filter === k ? 'bg-accent/15 text-accent' : 'text-faint hover:text-fg')}>
            {label}
          </button>
        ))}
        <button type="button" onClick={download} className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted hover:text-fg">
          <Download size={12} /> {t.exportReport}
        </button>
      </div>
      <ol className="flex-1 space-y-0.5 overflow-y-auto p-3 text-[11px]">
        {!shown.length && <li className="text-faint">{t.noTrace}</li>}
        {shown.map((e, i) => {
          const bad = ERRORS.has(e.kind) || e.status === 'error' || (e.kind === 'verify' && e.exitCode !== 0) || e.verdict === 'return';
          return (
            <li key={`${e.ts}-${i}`} className="flex min-w-0 gap-2">
              <span className="shrink-0 tabular-nums text-faint">{new Date(e.ts).toLocaleTimeString()}</span>
              <span className={cx('w-16 shrink-0 truncate', bad ? 'text-err' : e.kind === 'tool' ? 'text-muted' : 'text-accent')}>{e.kind}</span>
              <span className={cx('min-w-0 break-words', bad ? 'text-err/90' : 'text-fg/85')}>{describe(e)}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
