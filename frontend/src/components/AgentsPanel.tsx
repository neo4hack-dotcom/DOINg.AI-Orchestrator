import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import type { AgentRun, Flow, FlowTask } from '../types';
import { useT } from '../i18n';
import { cx, fmtDuration, fmtTokens } from '../util';
import { Markdown } from './Markdown';
import { TierChip, TierIcon } from './TierIcon';
import { Transcript } from './Transcript';

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function RunCard({ run, now, open, onToggle, onLoad }: { run: AgentRun; now: number; open: boolean; onToggle: () => void; onLoad: () => void }) {
  const t = useT();
  const active = run.status === 'running';
  const hasSteps = Boolean(run.stepTotal);
  const ratio = hasSteps ? Math.min(1, (run.stepDone || 0) / (run.stepTotal || 1)) : Math.min(1, run.contextTokens / (run.contextMax || 1));
  const elapsed = (run.endedAt || now) - run.startedAt;
  const [tab, setTab] = useState<'report' | 'transcript'>(active ? 'transcript' : 'report');
  useEffect(() => {
    if (open && !run.messages) onLoad();
  }, [open, run.messages]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className={cx('rounded-md border bg-panel', open ? 'border-accent/50' : 'border-line')}>
      <button type="button" onClick={onToggle} className="w-full px-2.5 py-2 text-left hover:bg-elev/60" aria-expanded={open}>
        <div className="flex min-w-0 items-center gap-2">
          <TierIcon tier={run.tier} active={active} />
          <span className="min-w-0 flex-1 truncate font-semibold">{run.description}</span>
          {run.round > 0 && <span className="shrink-0 text-[11px] text-warn">{t.round} {run.round + 1}</span>}
          {run.status === 'failed' && <span className="shrink-0 text-[11px] text-err">{t.failed}</span>}
          {open ? <ChevronDown size={13} className="shrink-0 text-faint" /> : <ChevronRight size={13} className="shrink-0 text-faint" />}
        </div>
        <div className="mt-0.5 flex min-w-0 items-center gap-1.5 pl-[26px] text-[11px] text-faint">
          <TierChip tier={run.tier} />
          <span className="truncate">{run.model}{run.effort ? ` · ${run.effort}` : ''}</span>
        </div>
        <div className="mt-1.5 pl-[26px]">
          <div className="h-1.5 overflow-hidden rounded-full bg-line">
            <div
              className={cx('h-full rounded-full transition-[width] duration-500', active && hasSteps && 'seg-running')}
              style={{ width: `${Math.max(ratio * 100, active ? 4 : 0)}%`, background: !hasSteps ? 'var(--faint)' : run.status === 'done' ? 'var(--ok)' : run.status === 'failed' ? 'var(--err)' : 'var(--accent)' }}
            />
          </div>
          <div className="mt-1 flex items-center gap-2 text-[11px] text-faint">
            <span className="min-w-0 flex-1 truncate">
              {hasSteps ? `${run.stepDone ?? 0}/${run.stepTotal} ${run.stepNote ? `· ${run.stepNote}` : ''}` : `${t.context} ${Math.round(ratio * 100)}%`}
            </span>
            <span className="shrink-0 tabular-nums">{run.steps} {t.steps}</span>
            <span className="shrink-0 tabular-nums">{fmtTokens(run.tokens)}</span>
            <span className="shrink-0 tabular-nums">{fmtDuration(elapsed)}</span>
          </div>
        </div>
      </button>
      {open && (
        <div className="border-t border-line px-2.5 py-2">
          <div className="mb-2 flex gap-3 text-[12px]">
            {(['report', 'transcript'] as const).map((k) => (
              <button key={k} type="button" onClick={() => setTab(k)} className={cx('border-b pb-0.5', tab === k ? 'border-accent text-fg' : 'border-transparent text-faint hover:text-muted')}>
                {k === 'report' ? t.report : t.transcript}
              </button>
            ))}
          </div>
          {run.verify && run.verify.length > 0 && (
            <div className="mb-2 space-y-0.5 text-[11px]">
              {run.verify.map((c, i) => (
                <div key={i} className={cx('truncate', c.exitCode === 0 ? 'text-ok' : 'text-err')}>
                  {c.exitCode === 0 ? '✓' : `✗ ${c.exitCode}`} <code>{c.command}</code>
                </div>
              ))}
            </div>
          )}
          {tab === 'report' ? (
            run.report ? <div className="text-[12px]"><Markdown text={run.report} /></div> : <div className="text-[12px] text-faint">{t.working}</div>
          ) : !run.messages ? (
            <Loader2 size={14} className="spin text-faint" />
          ) : (
            <div className="max-h-[55vh] overflow-y-auto text-[12px]">
              <Transcript messages={run.messages} busy={active} compact />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PlannedRow({ task }: { task: FlowTask }) {
  const t = useT();
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-md border border-dashed border-line px-2.5 py-1.5 text-muted">
      <TierIcon tier={task.tier} size={15} />
      <span className="min-w-0 flex-1 truncate">{task.n}. {task.title}</span>
      {task.status === 'blocked' ? (
        <span className="shrink-0 text-[11px] text-err">{t.blocked}</span>
      ) : task.after.length > 0 ? (
        <span className="shrink-0 text-[11px] text-faint">{t.after} {task.after.join(', ')}</span>
      ) : null}
      <TierChip tier={task.tier} />
    </div>
  );
}

export function AgentsPanel({ runs, flow, focusRunId, onLoadRun }: { runs: AgentRun[]; flow: Flow | null; focusRunId: string | null; onLoadRun: (id: string) => void }) {
  const t = useT();
  const running = runs.filter((r) => r.status === 'running');
  const finished = runs.filter((r) => r.status !== 'running').slice().reverse();
  const planned = flow && !flow.isFinished ? flow.tasks.filter((x) => x.status === 'planned' || x.status === 'blocked') : [];
  const now = useNow(running.length > 0);
  const [openId, setOpenId] = useState<string | null>(focusRunId);
  const [doneCollapsed, setDoneCollapsed] = useState(false);
  useEffect(() => {
    if (focusRunId) setOpenId(focusRunId);
  }, [focusRunId]);

  const tokens = runs.reduce((a, r) => a + r.tokens, 0);
  const time = runs.reduce((a, r) => a + ((r.endedAt || now) - r.startedAt), 0);
  const toggle = (id: string) => setOpenId((cur) => (cur === id ? null : id));

  return (
    <div className="flex h-full w-full flex-col">
      <div className="grid grid-cols-3 gap-px border-b border-line bg-line text-center text-[11px]">
        <div className="bg-bg py-1.5"><div className="text-faint">{t.tokens}</div><div className="tabular-nums text-fg">{fmtTokens(tokens)}</div></div>
        <div className="bg-bg py-1.5"><div className="text-faint">{t.time}</div><div className="tabular-nums text-fg">{fmtDuration(time)}</div></div>
        <div className="bg-bg py-1.5"><div className="text-faint">{t.cost}</div><div className="text-fg">$0 · {t.local}</div></div>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto p-3">
        {runs.length === 0 && planned.length === 0 && <p className="text-[12px] text-faint">{t.noAgents}</p>}
        {running.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-accent">{t.running} · {running.length}</h3>
            {running.map((r) => <RunCard key={r.id} run={r} now={now} open={openId === r.id} onToggle={() => toggle(r.id)} onLoad={() => onLoadRun(r.id)} />)}
          </section>
        )}
        {planned.length > 0 && (
          <section className="space-y-1.5">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-faint">{t.planned} · {planned.length}</h3>
            {planned.map((task) => <PlannedRow key={task.n} task={task} />)}
          </section>
        )}
        {finished.length > 0 && (
          <section className="space-y-2">
            <button type="button" onClick={() => setDoneCollapsed((c) => !c)} className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-faint hover:text-muted">
              {doneCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />} {t.finished} · {finished.length}
            </button>
            {!doneCollapsed && finished.map((r) => <RunCard key={r.id} run={r} now={now} open={openId === r.id} onToggle={() => toggle(r.id)} onLoad={() => onLoadRun(r.id)} />)}
          </section>
        )}
      </div>
    </div>
  );
}
