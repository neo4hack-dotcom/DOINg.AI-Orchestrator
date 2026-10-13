import { useEffect, useMemo, useRef, useState } from 'react';
import { Brain, ChevronRight } from 'lucide-react';
import { useT } from '../i18n';
import { cx, fmtDuration } from '../util';
import { Markdown } from './Markdown';

/* Splits a raw thought into readable steps: paragraphs, numbered / bulleted lines, or groups of sentences. */
export function thoughtSteps(text: string): string[] {
  const clean = text.replace(/\r/g, '').trim();
  if (!clean) return [];
  let steps = clean.split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean);
  if (steps.length === 1) {
    const lines = clean.split('\n').map((l) => l.trim()).filter(Boolean);
    if (lines.length > 1 && lines.filter((l) => /^(\d+[.)]|[-*•])\s/.test(l)).length >= 2) steps = lines;
    else if (clean.length > 420) {
      const sentences = clean.match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) || [clean];
      steps = [];
      for (let i = 0; i < sentences.length; i += 3) steps.push(sentences.slice(i, i + 3).join('').trim());
    }
  }
  if (steps.length > 40) steps = [...steps.slice(0, 39), steps.slice(39).join('\n\n')];
  return steps;
}

function useElapsed(start: number | undefined, live: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [live]);
  return start ? Math.max(0, now - start) : 0;
}

/* The model's reasoning: a shimmering live header with a rolling preview while it thinks,
   then a compact "Thought for Ns" pill that expands into a step timeline. */
export function ReasoningBlock({ text, live, startedAt, endedAt, agent }: {
  text: string; live: boolean; startedAt?: number; endedAt?: number; agent?: string;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const thinking = live && !endedAt;
  const elapsed = useElapsed(startedAt, thinking);
  const duration = endedAt && startedAt ? endedAt - startedAt : elapsed;
  const steps = useMemo(() => thoughtSteps(text), [text]);
  const preview = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = preview.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [text]);

  if (!text.trim()) return null;

  if (thinking) {
    return (
      <div className="my-1.5 rounded-md border border-accent/25 bg-accent/5 px-3 py-2" aria-live="polite">
        <div className="flex items-center gap-2 text-[12px]">
          <Brain size={13} className="pulse shrink-0 text-accent" />
          <span className="shimmer-text font-semibold">{t.thinkingLive}…</span>
          {agent && <span className="text-[11px] text-faint">{agent}</span>}
          <span className="ml-auto tabular-nums text-[11px] text-faint">{fmtDuration(duration)}</span>
        </div>
        <div ref={preview} className="fade-top mt-1.5 max-h-[4.6em] overflow-hidden whitespace-pre-wrap text-[12px] italic leading-[1.5] text-muted">
          {text.slice(-900)}
        </div>
      </div>
    );
  }

  return (
    <div className="my-1">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="group inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-panel px-2.5 py-0.5 text-[12px] text-muted hover:border-accent/40 hover:text-fg">
        <Brain size={12} className="shrink-0 text-accent/80" />
        <span className="shrink-0">{t.thoughtFor} {duration >= 1000 ? fmtDuration(duration) : '<1s'}</span>
        <span className="shrink-0 text-faint">· {steps.length} {steps.length === 1 ? t.thoughtStep : t.thoughtSteps}</span>
        {!open && <span className="min-w-0 truncate text-faint italic">— {steps[0]?.replace(/\s+/g, ' ').slice(0, 90)}</span>}
        <ChevronRight size={12} className={cx('shrink-0 text-faint transition-transform', open && 'rotate-90')} />
      </button>
      {open && (
        <ol className="ml-3.5 mt-2 space-y-2 border-l border-accent/25 pl-4">
          {steps.map((s, i) => (
            <li key={i} className="thought-step relative text-[12px] leading-[1.55] text-muted">
              <span className="mr-1.5 text-[10px] font-semibold uppercase tracking-wider text-accent/70">Step {i + 1}</span>
              <div className="md-thought"><Markdown text={s} /></div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
