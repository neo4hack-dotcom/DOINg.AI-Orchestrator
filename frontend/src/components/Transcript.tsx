import { memo, useState } from 'react';
import { AlertTriangle, Brain, Check, ChevronRight, Copy, GitBranch, Info, Layers, Pencil, RotateCcw, Target } from 'lucide-react';
import type { CompactionPart, Flow, Message, Part, ToolPart } from '../types';
import { useT } from '../i18n';
import { agentColor, cx, fmtTokens } from '../util';
import { Markdown } from './Markdown';
import { PlanCard } from './PlanCard';
import { ReviewCard } from './ReviewCard';
import { ToolPartView } from './ToolPartView';

function Reasoning({ text, live }: { text: string; live: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  if (!text.trim()) return null;
  return (
    <div className="my-1">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 px-1 text-faint hover:text-muted">
        <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
        <Brain size={12} className={live ? 'pulse' : ''} />
        <span className="italic">{t.thinking}</span>
      </button>
      {open && <div className="ml-6 mt-1 whitespace-pre-wrap border-l border-line pl-3 text-[12px] italic text-faint">{text}</div>}
    </div>
  );
}

function Checkpoint({ part }: { part: CompactionPart }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div className="my-1">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 px-1 text-[12px] text-faint hover:text-muted">
        <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
        <Layers size={12} /> {t.checkpoint}: {fmtTokens(part.before)} → {fmtTokens(part.after)}
      </button>
      {open && <div className="ml-6 mt-1 border-l border-line pl-3 text-[12px] text-muted"><Markdown text={part.text} /></div>}
    </div>
  );
}

function ToolGroup({ parts, defaultOpen }: { parts: ToolPart[]; defaultOpen: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(defaultOpen);
  const counts = parts.reduce<Record<string, number>>((acc, p) => ({ ...acc, [p.tool]: (acc[p.tool] || 0) + 1 }), {});
  const errors = parts.filter((p) => p.status === 'error').length;
  return (
    <div className="my-1">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-2 rounded px-1 py-0.5 text-left text-muted hover:bg-elev">
        <ChevronRight size={12} className={`shrink-0 text-faint transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="shrink-0 font-semibold text-fg">{parts.length} {t.toolCalls}</span>
        <span className="min-w-0 truncate text-[12px] text-faint">
          {Object.entries(counts).map(([k, v]) => `${k}${v > 1 ? ` ×${v}` : ''}`).join(' · ')}
        </span>
        {errors > 0 && <span className="shrink-0 text-[11px] text-err">{errors} ✗</span>}
      </button>
      {open && <div className="ml-3 border-l border-line pl-2">{parts.map((p) => <ToolPartView key={p.id} part={p} />)}</div>}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const t = useT();
  const [done, setDone] = useState(false);
  return (
    <button type="button" title={t.copy} onClick={() => {
      navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1200); }).catch(() => {});
    }} className="rounded p-1 text-faint hover:bg-elev hover:text-fg">
      {done ? <Check size={12} className="text-ok" /> : <Copy size={12} />}
    </button>
  );
}

interface Props {
  messages: Message[];
  flow?: Flow | null;
  busy: boolean;
  onApprove?: () => void;
  onOpenRun?: (runId: string) => void;
  onRetry?: (text: string, agent: string) => void;
  onEdit?: (text: string) => void;
  onFork?: (messageId: string) => void;
  compact?: boolean;
}

type Block =
  | { kind: 'user'; msg: Message }
  | { kind: 'part'; msg: Message; part: Part }
  | { kind: 'tools'; key: string; parts: ToolPart[]; live: boolean }
  | { kind: 'footer'; msg: Message };

const visibleText = (p: Part) => !((p.type === 'text' || p.type === 'reasoning') && (p.hidden || !p.text));

/* Consecutive tool calls (across assistant turns) fold into one group when there are many. */
function toBlocks(messages: Message[], busy: boolean, compact?: boolean): Block[] {
  const blocks: Block[] = [];
  let run: { parts: ToolPart[]; msg: Message }[] = [];
  const flush = (isTail: boolean) => {
    const parts = run.flatMap((r) => r.parts);
    if (parts.length >= 4 && !compact) {
      const live = parts.some((p) => p.status === 'running' || p.status === 'waiting') || (isTail && busy);
      blocks.push({ kind: 'tools', key: parts[0].id, parts, live });
    } else {
      for (const r of run) for (const p of r.parts) blocks.push({ kind: 'part', msg: r.msg, part: p });
    }
    run = [];
  };
  messages.forEach((m) => {
    if (m.role === 'user') {
      flush(false);
      if (m.parts.some(visibleText)) blocks.push({ kind: 'user', msg: m });
      return;
    }
    let toolsHere: ToolPart[] = [];
    for (const p of m.parts) {
      if (!visibleText(p)) continue;
      if (p.type === 'tool') {
        toolsHere.push(p);
        continue;
      }
      if (toolsHere.length) {
        run.push({ parts: toolsHere, msg: m });
        toolsHere = [];
      }
      flush(false);
      blocks.push({ kind: 'part', msg: m, part: p });
    }
    if (toolsHere.length) run.push({ parts: toolsHere, msg: m });
    const tokens = (m.tokens?.input || 0) + (m.tokens?.output || 0);
    if (m.model && tokens > 0 && m.parts.some((p) => p.type === 'text' && !p.hidden && p.text)) {
      flush(false);
      blocks.push({ kind: 'footer', msg: m });
    }
  });
  flush(true);
  return blocks;
}

export const Transcript = memo(function Transcript({ messages, flow = null, busy, onApprove, onOpenRun, onRetry, onEdit, onFork, compact }: Props) {
  const t = useT();
  const lastPlanId = [...messages].reverse().flatMap((m) => [...m.parts].reverse()).find((p) => p.type === 'plan')?.id;
  const lastMsgId = messages[messages.length - 1]?.id;
  const lastUserId = [...messages].reverse().find((m) => m.role === 'user' && !m.reverted)?.id;

  const renderPart = (m: Message, p: Part) => {
    switch (p.type) {
      case 'text':
        return (
          <div key={p.id} className="group/text relative px-1">
            <Markdown text={p.text} />
            {!compact && p.text.length > 40 && (
              <div className="absolute -top-1 right-0 opacity-0 transition-opacity group-hover/text:opacity-100"><CopyButton text={p.text} /></div>
            )}
          </div>
        );
      case 'reasoning':
        return <Reasoning key={p.id} text={p.text} live={busy && m.id === lastMsgId} />;
      case 'compaction':
        return <Checkpoint key={p.id} part={p} />;
      case 'notice':
        return (
          <div key={p.id} className="flex items-center gap-1.5 px-1 text-[12px] text-faint">
            <Info size={12} className="shrink-0" /> <span className="min-w-0 break-words">{p.text}</span>
          </div>
        );
      case 'error':
        return (
          <div key={p.id} className="my-1 flex items-start gap-2 rounded border border-err/40 bg-err/5 px-3 py-2 text-err">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" /> <span className="whitespace-pre-wrap">{p.text}</span>
          </div>
        );
      case 'tool':
        return <ToolPartView key={p.id} part={p} />;
      case 'plan':
        return <PlanCard key={p.id} part={p} flow={flow} isLatest={p.id === lastPlanId} onApprove={onApprove || (() => {})} busy={busy} />;
      case 'review':
        return <ReviewCard key={p.id} part={p} onOpenRun={onOpenRun || (() => {})} />;
      default:
        return null;
    }
  };

  const blocks = toBlocks(messages, busy, compact);
  return (
    <div className={compact ? 'space-y-1.5' : 'space-y-2.5'}>
      {blocks.map((b) => {
        if (b.kind === 'tools') return <ToolGroup key={b.key} parts={b.parts} defaultOpen={b.live} />;
        if (b.kind === 'footer') {
          const tokens = (b.msg.tokens?.input || 0) + (b.msg.tokens?.output || 0);
          if (compact) return null;
          return (
            <div key={`f-${b.msg.id}`} className="flex items-center gap-1.5 px-1 text-[11px] text-faint">
              <span style={{ color: agentColor(b.msg.agent) }}>▣</span> {b.msg.agent} · {b.msg.model} · {fmtTokens(tokens)} tok
            </div>
          );
        }
        const m = b.msg;
        const reverted = Boolean(m.reverted);
        if (b.kind === 'user') {
          const text = m.parts.map((p) => (p.type === 'text' ? p.text : '')).join('\n');
          const goal = m.parts.some((p) => p.type === 'text' && p.goal);
          return (
            <div key={m.id} className={cx('group relative rounded-r border-l-2 bg-panel px-3 py-2', reverted && 'opacity-40', !compact && 'mt-4')}
              style={{ borderColor: agentColor(m.agent) }}>
              {goal && <div className="mb-0.5 flex items-center gap-1 text-[11px] text-accent"><Target size={11} /> {t.goal}</div>}
              <div className={cx('whitespace-pre-wrap break-words', goal && 'text-muted')}>{text}</div>
              {reverted && <div className="mt-1 text-[11px] text-faint">{t.reverted}</div>}
              {!compact && !reverted && !busy && (
                <div className="absolute right-1.5 top-1.5 flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                  {onEdit && m.id === lastUserId && (
                    <button type="button" title={t.editResend} onClick={() => onEdit(text)} className="rounded p-1 text-faint hover:bg-elev hover:text-fg"><Pencil size={12} /></button>
                  )}
                  {onRetry && (
                    <button type="button" title={t.retry} onClick={() => onRetry(text, m.agent)} className="rounded p-1 text-faint hover:bg-elev hover:text-fg"><RotateCcw size={12} /></button>
                  )}
                  {onFork && (
                    <button type="button" title={t.forkHere} onClick={() => onFork(m.id)} className="rounded p-1 text-faint hover:bg-elev hover:text-fg"><GitBranch size={12} /></button>
                  )}
                  <CopyButton text={text} />
                </div>
              )}
            </div>
          );
        }
        const node = renderPart(m, b.part);
        return node ? <div key={b.part.id} className={cx(reverted && 'opacity-40')}>{node}</div> : null;
      })}
    </div>
  );
});
