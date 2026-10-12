import { Play, Users, X } from 'lucide-react';
import type { AgentRun, Flow } from '../types';
import { useT } from '../i18n';
import { cx } from '../util';
import { TierIcon } from './TierIcon';

/* savvy-progress: the flow's title, phase, accepted tasks out of planned, and a
   button with the crew size that opens the agents panel. */
export function ProgressBar({ flow, runs, panelOpen, onTogglePanel, onApprove, onDismiss, busy }: {
  flow: Flow | null;
  runs: AgentRun[];
  panelOpen: boolean;
  onTogglePanel: () => void;
  onApprove: () => void;
  onDismiss: () => void;
  busy: boolean;
}) {
  const t = useT();
  const crew = flow ? runs.filter((r) => r.flowId === flow.id) : runs;
  const running = crew.filter((r) => r.status === 'running');
  const lead = running[0];

  let label: string;
  if (!flow) label = `${running.length} ${t.busy}`;
  else if (flow.isFinished) label = t.phaseDone;
  else if (flow.phase === 'plan') label = t.phasePlan;
  else if (flow.phase === 'design') label = t.phaseDesign;
  else {
    const count = flow.total ? `${flow.done}/${flow.total}` : `${flow.running} ${t.busy}`;
    label = `${flow.phase === 'review' ? t.phaseReview : t.phaseTasks} ${count}`;
  }
  const ratio = !flow ? 0 : flow.isFinished ? 1 : flow.total ? flow.done / flow.total : 0;
  const awaiting = flow && !flow.isFinished && (flow.awaiting === 'approval' || flow.interrupted);

  return (
    <div className="flex min-w-0 items-center gap-2.5 rounded-md border border-line bg-panel px-2.5 py-1.5">
      <TierIcon tier={lead?.tier} active={Boolean(lead)} size={16} />
      <span className="max-w-[30%] shrink-0 truncate font-semibold text-fg">{flow ? flow.title : 'savvy'}</span>
      <span className={cx('shrink-0 text-[12px]', flow?.isFinished ? 'text-ok' : 'text-accent')}>{label}</span>
      {flow?.awaiting === 'answers' && !flow.isFinished && <span className="hidden shrink-0 text-[11px] text-warn sm:inline">· {t.awaitingAnswers}</span>}

      <div className="flex h-2.5 min-w-[60px] flex-1 gap-[3px]" role="progressbar" aria-valuenow={Math.round(ratio * 100)} aria-valuemin={0} aria-valuemax={100}>
        {flow && flow.tasks.length > 0 ? (
          flow.tasks.map((task) => (
            <div
              key={task.n}
              title={`${task.n}. ${task.title} · ${task.tier} · ${task.status}`}
              className={cx(
                'h-full flex-1 rounded-[2px]',
                task.status === 'running' && 'seg-running',
                task.status === 'review' && 'seg-review',
              )}
              style={{
                background:
                  task.status === 'accepted' ? 'var(--ok)'
                  : task.status === 'running' || task.status === 'review' ? 'var(--accent)'
                  : task.status === 'failed' ? 'var(--err)'
                  : task.status === 'blocked' ? 'color-mix(in srgb, var(--err) 35%, var(--line))'
                  : 'var(--line)',
              }}
            />
          ))
        ) : (
          <div className="h-full flex-1 overflow-hidden rounded-[2px] bg-line">
            <div className={cx('h-full', !flow?.isFinished && 'seg-running')} style={{ width: `${Math.max(ratio * 100, flow?.isFinished ? 100 : 12)}%`, background: flow?.isFinished ? 'var(--ok)' : 'var(--accent)' }} />
          </div>
        )}
      </div>
      <span className="w-9 shrink-0 text-right text-[12px] tabular-nums text-muted">{Math.round(ratio * 100)}%</span>

      {awaiting && (
        <button type="button" disabled={busy} onClick={onApprove} className="inline-flex shrink-0 items-center gap-1 rounded bg-flow px-2 py-0.5 text-[12px] font-semibold text-bg disabled:opacity-50">
          <Play size={11} /> {flow!.interrupted ? t.resume : t.approve}
        </button>
      )}
      <button
        type="button"
        onClick={onTogglePanel}
        aria-pressed={panelOpen}
        title={t.agents}
        className={cx('inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[12px]', panelOpen ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}
      >
        <Users size={12} /> {crew.length}
        {running.length > 0 && <span className="pulse text-accent">●</span>}
      </button>
      {flow?.isFinished && (
        <button type="button" onClick={onDismiss} title={t.dismiss} className="shrink-0 text-faint hover:text-fg">
          <X size={14} />
        </button>
      )}
    </div>
  );
}
