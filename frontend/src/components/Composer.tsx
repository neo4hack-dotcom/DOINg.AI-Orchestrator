import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, Square } from 'lucide-react';
import type { AgentMode } from '../types';
import { useT, type Strings } from '../i18n';
import { api } from '../api';
import { agentColor, cx } from '../util';

export const AGENT_ORDER: AgentMode[] = ['build', 'plan', 'flow'];

const COMMANDS: { name: string; desc: keyof Strings; arg?: boolean }[] = [
  { name: 'flow', desc: 'cmdFlow', arg: true },
  { name: 'new', desc: 'cmdNew' },
  { name: 'build', desc: 'cmdBuild' },
  { name: 'plan', desc: 'cmdPlan' },
  { name: 'agents', desc: 'cmdAgents' },
  { name: 'init', desc: 'cmdInit' },
  { name: 'settings', desc: 'cmdSettings' },
  { name: 'help', desc: 'cmdHelp' },
];

type Item = { key: string; label: string; hint?: string; apply: () => void };

export function Composer({ sessionId, agent, onAgentChange, busy, onSend, onAbort, onCommand, model, answering }: {
  sessionId: string | null;
  agent: AgentMode;
  onAgentChange: (a: AgentMode) => void;
  busy: boolean;
  onSend: (text: string, agent: AgentMode) => void;
  onAbort: () => void;
  onCommand: (name: string) => void;
  model: string;
  answering: boolean;
}) {
  const t = useT();
  const [text, setText] = useState('');
  const [files, setFiles] = useState<string[]>([]);
  const [sel, setSel] = useState(0);
  const [closedFor, setClosedFor] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  useEffect(() => {
    ref.current?.focus();
  }, [sessionId, busy]);

  const mention = /(^|\s)@([\w./-]*)$/.exec(text);
  const slash = /^\/(\w*)$/.exec(text);

  useEffect(() => {
    if (!mention || !sessionId) return;
    const q = mention[2];
    const id = setTimeout(() => {
      api.files(sessionId, q).then(setFiles).catch(() => setFiles([]));
    }, 120);
    return () => clearTimeout(id);
  }, [mention?.[2], sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  const items: Item[] = useMemo(() => {
    if (closedFor === text) return [];
    if (slash) {
      return COMMANDS.filter((c) => c.name.startsWith(slash[1])).map((c) => ({
        key: c.name,
        label: `/${c.name}${c.arg ? ' <task>' : ''}`,
        hint: t[c.desc],
        apply: () => {
          if (c.arg) setText(`/${c.name} `);
          else {
            setText('');
            onCommand(c.name);
          }
        },
      }));
    }
    if (mention) {
      return files.slice(0, 12).map((f) => ({
        key: f,
        label: `@${f}`,
        apply: () => setText(text.slice(0, text.length - mention[2].length - 1) + `@${f} `),
      }));
    }
    return [];
  }, [text, files, closedFor]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => setSel(0), [items.length]);

  const submit = () => {
    const value = text.trim();
    if (!value || busy) return;
    const cmd = /^\/(\w+)\s*$/.exec(value);
    if (cmd && COMMANDS.some((c) => c.name === cmd[1] && !c.arg)) {
      setText('');
      onCommand(cmd[1]);
      return;
    }
    setText('');
    onSend(value, value.startsWith('/flow') ? 'flow' : agent);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (items.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => (s + 1) % items.length); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => (s - 1 + items.length) % items.length); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); items[sel]?.apply(); return; }
      if (e.key === 'Escape') { e.preventDefault(); setClosedFor(text); return; }
    }
    if (e.key === 'Tab' && !e.shiftKey) {
      e.preventDefault();
      onAgentChange(AGENT_ORDER[(AGENT_ORDER.indexOf(agent) + 1) % AGENT_ORDER.length]);
      return;
    }
    if (e.key === 'Escape' && busy) { e.preventDefault(); onAbort(); return; }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  };

  const color = agentColor(agent);
  const agentLabel = agent === 'build' ? t.agentBuild : agent === 'plan' ? t.agentPlan : t.agentFlow;
  const placeholder = answering ? t.placeholderAnswer : agent === 'plan' ? t.placeholderPlan : agent === 'flow' ? t.placeholderFlow : t.placeholderBuild;

  return (
    <div className="relative">
      {items.length > 0 && (
        <ul className="absolute bottom-full left-0 right-0 mb-1.5 max-h-72 overflow-y-auto rounded-md border border-line bg-elev py-1 shadow-2xl" role="listbox">
          {items.map((it, i) => (
            <li key={it.key} role="option" aria-selected={i === sel}>
              <button
                type="button"
                onMouseDown={(e) => { e.preventDefault(); it.apply(); }}
                onMouseEnter={() => setSel(i)}
                className={cx('flex w-full min-w-0 items-baseline gap-3 px-3 py-1 text-left', i === sel && 'bg-accent/15')}
              >
                <span className={cx('shrink-0', i === sel ? 'text-accent' : 'text-fg')}>{it.label}</span>
                {it.hint && <span className="truncate text-[12px] text-faint">{it.hint}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="rounded-md border bg-panel transition-colors focus-within:bg-elev" style={{ borderColor: `color-mix(in srgb, ${color} 55%, var(--line))` }}>
        <div className="flex items-end gap-2 px-3 pt-2.5">
          <span className="select-none pb-1 font-bold" style={{ color }}>›</span>
          <textarea
            ref={ref}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            aria-label={placeholder}
            className="max-h-[220px] min-h-[24px] flex-1 resize-none bg-transparent py-0.5 text-fg outline-none placeholder:text-faint focus-visible:outline-none"
          />
          {busy ? (
            <button type="button" onClick={onAbort} title={t.stop} className="mb-0.5 rounded bg-err/80 p-1.5 text-bg hover:bg-err">
              <Square size={13} fill="currentColor" />
            </button>
          ) : (
            <button type="button" onClick={submit} disabled={!text.trim()} title={t.send} className="mb-0.5 rounded p-1.5 text-bg disabled:opacity-30" style={{ background: color }}>
              <ArrowUp size={14} strokeWidth={2.5} />
            </button>
          )}
        </div>
        <div className="flex min-w-0 items-center gap-3 px-3 pb-2 pt-1.5 text-[11px]">
          <button
            type="button"
            onClick={() => onAgentChange(AGENT_ORDER[(AGENT_ORDER.indexOf(agent) + 1) % AGENT_ORDER.length])}
            className="shrink-0 rounded px-1.5 font-bold uppercase tracking-wider"
            style={{ color: 'var(--bg)', background: color }}
          >
            {agentLabel}
          </button>
          <span className="min-w-0 truncate text-muted">{model}</span>
          {busy && <span className="pulse shrink-0 text-accent">{t.working}</span>}
          <span className="ml-auto hidden shrink-0 gap-3 text-faint md:flex">
            <span>{t.hintTab}</span><span>{t.hintCmd}</span><span>{t.hintFile}</span><span>{t.hintEsc}</span>
          </span>
        </div>
      </div>
    </div>
  );
}
