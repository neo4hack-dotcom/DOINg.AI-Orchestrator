import { CheckCircle2, Loader2, Undo2 } from 'lucide-react';
import type { ReviewPart } from '../types';
import { useT } from '../i18n';
import { TierChip } from './TierIcon';

export function ReviewCard({ part, onOpenRun }: { part: ReviewPart; onOpenRun: (runId: string) => void }) {
  const t = useT();
  const accepted = part.verdict === 'accept';
  const color = part.status === 'reviewing' ? 'var(--accent)' : accepted ? 'var(--ok)' : 'var(--warn)';
  return (
    <div className="my-2 rounded-md border bg-panel px-3 py-2" style={{ borderColor: `color-mix(in srgb, ${color} 45%, transparent)` }}>
      <div className="flex min-w-0 items-center gap-2">
        {part.status === 'reviewing' ? (
          <Loader2 size={14} className="spin" style={{ color }} />
        ) : accepted ? (
          <CheckCircle2 size={14} style={{ color }} />
        ) : (
          <Undo2 size={14} style={{ color }} />
        )}
        <span className="shrink-0 font-bold">{t.review}</span>
        <button type="button" onClick={() => onOpenRun(part.runId)} className="min-w-0 truncate text-left text-fg/90 hover:underline">
          {part.taskN}. {part.title}
        </button>
        <TierChip tier={part.tier} />
        {part.round > 0 && <span className="shrink-0 text-[11px] text-faint">{t.round} {part.round + 1}</span>}
        <span className="ml-auto shrink-0 text-[12px] font-semibold" style={{ color }}>
          {part.status === 'reviewing' ? t.reviewing : part.auto ? t.autoAccepted : accepted ? t.accepted : t.returned}
        </span>
      </div>
      {part.files.length > 0 && <div className="mt-1 truncate pl-6 text-[11px] text-faint">{part.files.join('  ')}</div>}
      {part.checks && part.checks.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-x-3 pl-6 text-[11px]">
          {part.checks.map((c, i) => (
            <span key={i} className={c.exitCode === 0 ? 'text-ok' : 'text-err'}>{c.exitCode === 0 ? '✓' : `✗${c.exitCode}`} <code>{c.command.slice(0, 60)}</code></span>
          ))}
        </div>
      )}
      {part.note && <div className="mt-1 pl-6 text-[12px] text-muted">{part.note}</div>}
      {part.defects.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-10 text-[12px] text-warn/90">
          {part.defects.map((d, i) => <li key={i}>{d}</li>)}
        </ul>
      )}
    </div>
  );
}
