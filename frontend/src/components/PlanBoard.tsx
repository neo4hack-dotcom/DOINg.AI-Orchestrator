import { useMemo } from 'react';
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, Lock, Wrench } from 'lucide-react';
import type { AgentRun, Flow, FlowTask } from '../types';
import { useT } from '../i18n';
import { cx, fmtDuration, fmtTokens } from '../util';
import { TierChip, TierIcon } from './TierIcon';

const STATUS_COLOR: Record<FlowTask['status'], string> = {
  planned: 'var(--faint)',
  running: 'var(--accent)',
  review: 'var(--accent)',
  accepted: 'var(--ok)',
  failed: 'var(--err)',
  blocked: 'var(--err)',
};

function StatusIcon({ status }: { status: FlowTask['status'] }) {
  const c = STATUS_COLOR[status];
  if (status === 'accepted') return <CheckCircle2 size={13} style={{ color: c }} />;
  if (status === 'running' || status === 'review') return <Loader2 size={13} className="spin" style={{ color: c }} />;
  if (status === 'failed') return <AlertTriangle size={13} style={{ color: c }} />;
  if (status === 'blocked') return <Lock size={13} style={{ color: c }} />;
  return <CircleDashed size={13} style={{ color: c }} />;
}

/* The flow as a board: modules (homogeneous sub-parts) with their tasks, waves,
   critical path, file ownership, checks and cost per task. */
export function PlanBoard({ flow, runs, onOpenRun }: { flow: Flow | null; runs: AgentRun[]; onOpenRun: (id: string) => void }) {
  const t = useT();
  const runsById = useMemo(() => Object.fromEntries(runs.map((r) => [r.id, r])), [runs]);
  if (!flow || !flow.tasks.length) return <p className="p-3 text-[12px] text-faint">{t.noPlan}</p>;

  const groups: { id: string; name: string; goal?: string; gate?: number; tasks: FlowTask[] }[] = [];
  const mods = flow.modules || [];
  for (const m of mods) groups.push({ id: m.id, name: m.name, goal: m.goal, gate: m.gate, tasks: [] });
  const other = { id: '', name: flow.title, tasks: [] as FlowTask[] };
  for (const task of flow.tasks) {
    const g = groups.find((x) => x.id === task.module);
    (g ? g.tasks : other.tasks).push(task);
  }
  if (other.tasks.length) groups.push(other);
  const maxPriority = Math.max(...flow.tasks.map((x) => x.priority || 0), 1);
  const stats = flow.stats;

  return (
    <div className="space-y-3 p-3">
      {stats && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-faint">
          <span><b className="text-fg">{stats.waves}</b> {t.waves}</span>
          <span><b className="text-fg">{stats.maxWidth}</b> {t.maxWidth}</span>
          <span>{t.criticalPath} <b className="text-fg">{stats.criticalPath}</b></span>
          {stats.conflictEdges > 0 && <span><b className="text-fg">{stats.conflictEdges}</b> {t.conflictEdges}</span>}
          {stats.merged > 0 && <span><b className="text-fg">{stats.merged}</b> {t.mergedTasks}</span>}
          {(flow.tokens || 0) > 0 && <span><b className="text-fg">{fmtTokens(flow.tokens || 0)}</b> {t.flowTokens}</span>}
          {flow.budgetExceeded && <span className="text-err">{t.budgetReached}</span>}
        </div>
      )}
      {groups.map((g) => {
        const done = g.tasks.filter((x) => x.status === 'accepted').length;
        return (
          <section key={g.id || 'all'} className="rounded-md border border-line bg-panel">
            <header className="flex min-w-0 items-center gap-2 border-b border-line px-2.5 py-1.5">
              <span className="min-w-0 flex-1 truncate font-semibold">{g.id ? `${g.id} · ${g.name}` : g.name}</span>
              {g.gate !== undefined && (
                <span className={cx('shrink-0 text-[11px]', g.gate === 0 ? 'text-ok' : 'text-err')}>gate {g.gate === 0 ? '✓' : `exit ${g.gate}`}</span>
              )}
              <span className="shrink-0 text-[11px] tabular-nums text-faint">{done}/{g.tasks.length}</span>
            </header>
            {g.goal && <p className="px-2.5 pt-1.5 text-[11px] text-faint">{g.goal}</p>}
            <ul className="divide-y divide-line">
              {g.tasks.map((task) => {
                const last = task.runIds.length ? runsById[task.runIds[task.runIds.length - 1]] : undefined;
                const tokens = task.runIds.reduce((a, id) => a + (runsById[id]?.tokens || 0), 0);
                const dur = task.startedAt ? (task.endedAt || Date.now()) - task.startedAt : 0;
                const critical = (task.priority || 0) >= maxPriority - 0.01;
                return (
                  <li key={task.n}>
                    <button
                      type="button"
                      disabled={!last}
                      onClick={() => last && onOpenRun(last.id)}
                      className="w-full px-2.5 py-1.5 text-left enabled:hover:bg-elev"
                    >
                      <div className="flex min-w-0 items-center gap-1.5">
                        <StatusIcon status={task.status} />
                        <span className="w-6 shrink-0 text-right text-[11px] text-faint">{task.n}.</span>
                        <TierIcon tier={task.tier} active={task.status === 'running'} size={13} />
                        <span className={cx('min-w-0 flex-1 truncate', task.status === 'accepted' ? 'text-muted' : 'text-fg')}>{task.title}</span>
                        {task.fix && <Wrench size={11} className="shrink-0 text-warn" />}
                        {critical && task.status !== 'accepted' && <span className="shrink-0 text-[10px] text-accent" title={t.criticalPath}>◆</span>}
                        <TierChip tier={task.tier} />
                      </div>
                      <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 pl-[52px] text-[10.5px] text-faint">
                        <span>{t.wave} {task.wave ?? '-'}</span>
                        {task.size && <span>{task.size}·c{task.complexity}</span>}
                        {task.after.length > 0 && <span>{t.after} {task.after.join(',')}</span>}
                        {task.round > 0 && <span className="text-warn">{t.round} {task.round + 1}</span>}
                        {(task.merged || 1) > 1 && <span>×{task.merged}</span>}
                        {task.checks?.length ? (
                          <span className={task.checks[task.checks.length - 1].exitCode === 0 ? 'text-ok' : 'text-err'}>
                            check {task.checks.map((c) => (c.exitCode === 0 ? '✓' : '✗')).join('')}
                          </span>
                        ) : null}
                        {tokens > 0 && <span className="tabular-nums">{fmtTokens(tokens)}</span>}
                        {dur > 0 && <span className="tabular-nums">{fmtDuration(dur)}</span>}
                      </div>
                      {task.files && task.files.length > 0 && (
                        <div className="truncate pl-[52px] text-[10.5px] text-faint/80">{task.files.join('  ')}</div>
                      )}
                      {task.status === 'failed' && task.note && <div className="pl-[52px] text-[11px] text-err/90">{task.note}</div>}
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
      {flow.integration && flow.integration.length > 0 && (
        <section className="rounded-md border border-line bg-panel px-2.5 py-2 text-[11px]">
          <div className="mb-1 font-semibold">{t.integration}</div>
          {flow.integration.map((c, i) => (
            <div key={i} className={cx('truncate', c.exitCode === 0 ? 'text-ok' : 'text-err')}>
              {c.exitCode === 0 ? '✓' : `✗ ${c.exitCode}`} <code>{c.command}</code>
            </div>
          ))}
        </section>
      )}
      {flow.notes && flow.notes.length > 0 && (
        <section className="rounded-md border border-line bg-panel px-2.5 py-2 text-[11px]">
          <div className="mb-1 font-semibold">{t.notes}</div>
          <ul className="space-y-0.5 text-muted">
            {flow.notes.slice(-20).map((n, i) => (
              <li key={i}><span className="text-faint">[{n.author}]</span> {n.text}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
