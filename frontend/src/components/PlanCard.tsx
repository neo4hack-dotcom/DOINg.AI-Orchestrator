import { useState } from 'react';
import { ChevronRight, ListChecks, MessageCircleQuestion, Play } from 'lucide-react';
import type { Flow, PlanPart } from '../types';
import { useT } from '../i18n';
import { Markdown } from './Markdown';
import { TierChip, TierIcon } from './TierIcon';

export function PlanCard({ part, flow, isLatest, onApprove, busy }: {
  part: PlanPart;
  flow: Flow | null;
  isLatest: boolean;
  onApprove: () => void;
  busy: boolean;
}) {
  const t = useT();
  const [openTask, setOpenTask] = useState<number | null>(null);
  const [showSpec, setShowSpec] = useState(false);
  const live = isLatest && flow && flow.id === part.flowId && !flow.isFinished;
  const awaitingApproval = live && (flow!.awaiting === 'approval' || flow!.interrupted);
  const statusOf = (n: number) => (live || flow?.id === part.flowId ? flow?.tasks.find((x) => x.n === n)?.status : undefined);

  return (
    <div className="my-2 overflow-hidden rounded-md border border-flow/40 bg-panel">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <ListChecks size={14} className="text-flow" />
        <span className="font-bold text-fg">{t.plan}</span>
        <span className="truncate text-muted">· {part.title}</span>
        {live && flow!.awaiting === 'approval' && <span className="ml-auto shrink-0 text-[11px] text-warn">{t.awaitingApproval}</span>}
        {live && flow!.awaiting === 'answers' && <span className="ml-auto shrink-0 text-[11px] text-warn">{t.awaitingAnswers}</span>}
        {live && flow!.interrupted && <span className="ml-auto shrink-0 text-[11px] text-err">{t.interrupted}</span>}
      </div>
      <div className="space-y-3 px-3 py-2.5">
        {part.summary && <p className="text-fg/90">{part.summary}</p>}

        {part.questions.length > 0 && (
          <div className="rounded border border-warn/40 bg-warn/5 px-3 py-2">
            <div className="mb-1 flex items-center gap-1.5 font-semibold text-warn">
              <MessageCircleQuestion size={13} /> {t.questions}
            </div>
            <ol className="list-decimal space-y-0.5 pl-5 text-fg/90">
              {part.questions.map((q, i) => <li key={i}>{q}</li>)}
            </ol>
          </div>
        )}

        {part.stats && part.tasks.length > 0 && (
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-faint">
            {part.mode === 'modular' && <span><b className="text-fg">{part.modules?.length}</b> {t.modules.toLowerCase()}</span>}
            <span><b className="text-fg">{part.tasks.length}</b> {t.tasks.toLowerCase()}</span>
            <span><b className="text-fg">{part.stats.waves}</b> {t.waves}</span>
            <span><b className="text-fg">{part.stats.maxWidth}</b> {t.maxWidth}</span>
            {part.stats.conflictEdges > 0 && <span><b className="text-fg">{part.stats.conflictEdges}</b> {t.conflictEdges}</span>}
            {part.stats.merged > 0 && <span><b className="text-fg">{part.stats.merged}</b> {t.mergedTasks}</span>}
          </div>
        )}

        {part.modules && part.modules.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {part.modules.map((m) => (
              <span key={m.id} title={m.goal} className="rounded border border-line px-1.5 py-0.5 text-[11px] text-muted">
                <b className="text-fg">{m.id}</b> {m.name}{m.dependsOn.length > 0 && <span className="text-faint"> ← {m.dependsOn.join(', ')}</span>}
              </span>
            ))}
          </div>
        )}

        {part.tasks.length > 0 && (
          <ol className="max-h-[420px] divide-y divide-line overflow-y-auto rounded border border-line">
            {part.tasks.map((task) => {
              const st = statusOf(task.n);
              const open = openTask === task.n;
              return (
                <li key={task.n}>
                  <button
                    type="button"
                    onClick={() => setOpenTask(open ? null : task.n)}
                    className="flex w-full min-w-0 items-center gap-2 px-2 py-1.5 text-left hover:bg-elev"
                  >
                    <ChevronRight size={12} className={`shrink-0 text-faint transition-transform ${open ? 'rotate-90' : ''}`} />
                    <span className="w-5 shrink-0 text-right text-faint">{task.n}.</span>
                    <TierIcon tier={task.tier} active={st === 'running'} size={14} />
                    <span className={`min-w-0 truncate ${st === 'accepted' ? 'text-muted line-through decoration-faint' : 'text-fg'}`}>{task.title}</span>
                    <span className="ml-auto flex shrink-0 items-center gap-2">
                      {task.module && <span className="hidden text-[11px] text-faint sm:inline">{task.module}</span>}
                      {task.wave !== undefined && <span className="text-[11px] text-faint">{t.wave} {task.wave}</span>}
                      {task.size && <span className="text-[11px] text-faint">{task.size}·c{task.complexity}</span>}
                      {task.after.length > 0 && <span className="hidden text-[11px] text-faint sm:inline">{t.after} {task.after.join(', ')}</span>}
                      <TierChip tier={task.tier} />
                    </span>
                  </button>
                  {open && (
                    <div className="space-y-1 px-9 pb-2 text-[12px] text-muted">
                      <div className="whitespace-pre-wrap">{task.brief}</div>
                      {task.verify && <div><span className="text-faint">{t.verify}:</span> {task.verify}</div>}
                      {task.verifyCommand && <div><span className="text-faint">$</span> <code>{task.verifyCommand}</code></div>}
                      {task.files && task.files.length > 0 && <div className="text-faint">{task.files.join('  ')}</div>}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}

        {part.verifyCommands && part.verifyCommands.length > 0 && (
          <div className="text-[11px] text-faint">{t.integration}: {part.verifyCommands.map((c) => <code key={c} className="mr-2 text-muted">{c}</code>)}</div>
        )}

        {part.design && (
          <details className="rounded border border-line px-3 py-1.5">
            <summary className="cursor-pointer text-muted">{t.design}</summary>
            <div className="pt-1.5"><Markdown text={part.design} /></div>
          </details>
        )}
        {part.spec && (
          <div>
            <button type="button" onClick={() => setShowSpec((s) => !s)} className="text-muted underline decoration-faint underline-offset-2 hover:text-fg">
              docs/SPEC.md
            </button>
            {showSpec && <div className="mt-1.5 rounded border border-line px-3 py-2"><Markdown text={part.spec} /></div>}
          </div>
        )}

        {awaitingApproval && part.tasks.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button
              type="button"
              disabled={busy}
              onClick={onApprove}
              className="inline-flex items-center gap-1.5 rounded bg-flow px-3 py-1.5 font-semibold text-bg hover:brightness-110 disabled:opacity-50"
            >
              <Play size={13} /> {flow!.interrupted ? t.resume : t.approve}
            </button>
            <span className="text-[12px] text-faint">{t.placeholderAnswer}</span>
          </div>
        )}
      </div>
    </div>
  );
}
