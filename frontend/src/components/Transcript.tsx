import { memo, useState } from 'react';
import { AlertTriangle, Brain, ChevronRight, Info } from 'lucide-react';
import type { Flow, Message, Part } from '../types';
import { useT } from '../i18n';
import { agentColor, fmtTokens } from '../util';
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

interface Props {
  messages: Message[];
  flow?: Flow | null;
  busy: boolean;
  onApprove?: () => void;
  onOpenRun?: (runId: string) => void;
  compact?: boolean;
}

export const Transcript = memo(function Transcript({ messages, flow = null, busy, onApprove, onOpenRun, compact }: Props) {
  const lastPlanId = [...messages].reverse().flatMap((m) => [...m.parts].reverse()).find((p) => p.type === 'plan')?.id;
  const lastMsgId = messages[messages.length - 1]?.id;

  const renderPart = (m: Message, p: Part) => {
    switch (p.type) {
      case 'text':
        if (p.hidden || !p.text) return null;
        return m.role === 'user' ? (
          <div key={p.id} className="whitespace-pre-wrap break-words">{p.text}</div>
        ) : (
          <div key={p.id} className="px-1"><Markdown text={p.text} /></div>
        );
      case 'reasoning':
        return <Reasoning key={p.id} text={p.text} live={busy && m.id === lastMsgId} />;
      case 'notice':
        return (
          <div key={p.id} className="flex items-center gap-1.5 px-1 text-[12px] text-faint">
            <Info size={12} /> {p.text}
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

  return (
    <div className={compact ? 'space-y-2' : 'space-y-4'}>
      {messages.map((m) => {
        const visible = m.parts.filter((p) => !(p.type === 'text' && (p.hidden || !p.text)));
        if (!visible.length) return null;
        if (m.role === 'user') {
          return (
            <div key={m.id} className="rounded-r border-l-2 bg-panel px-3 py-2" style={{ borderColor: agentColor(m.agent) }}>
              {m.parts.map((p) => renderPart(m, p))}
            </div>
          );
        }
        const tokens = (m.tokens?.input || 0) + (m.tokens?.output || 0);
        return (
          <div key={m.id} className="group">
            {m.parts.map((p) => renderPart(m, p))}
            {!compact && m.model && tokens > 0 && (
              <div className="mt-1 flex items-center gap-1.5 px-1 text-[11px] text-faint opacity-0 transition-opacity group-hover:opacity-100">
                <span style={{ color: agentColor(m.agent) }}>▣</span> {m.agent} · {m.model} · {fmtTokens(tokens)} tok
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
});
