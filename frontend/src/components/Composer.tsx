import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, Bot, ListPlus, ShieldCheck, Square, SquareTerminal, Target, X, Zap } from 'lucide-react';
import type { AgentMode, CommandInfo, Goal, PermissionMode } from '../types';
import { useT, type Strings } from '../i18n';
import { api } from '../api';
import { agentColor, cx, fmtTokens } from '../util';

export const AGENT_ORDER: AgentMode[] = ['build', 'plan', 'flow'];
export const MODE_ORDER: PermissionMode[] = ['ask', 'acceptEdits', 'auto'];

export const COMMANDS: { name: string; desc: keyof Strings; arg?: boolean }[] = [
  { name: 'flow', desc: 'cmdFlow', arg: true },
  { name: 'goal', desc: 'cmdGoal', arg: true },
  { name: 'new', desc: 'cmdNew' },
  { name: 'build', desc: 'cmdBuild' },
  { name: 'plan', desc: 'cmdPlan' },
  { name: 'pr', desc: 'cmdPr' },
  { name: 'undo', desc: 'cmdUndo' },
  { name: 'compact', desc: 'cmdCompact' },
  { name: 'fork', desc: 'cmdFork' },
  { name: 'auto', desc: 'cmdAuto' },
  { name: 'terminal', desc: 'cmdTerminal' },
  { name: 'files', desc: 'cmdFiles' },
  { name: 'preview', desc: 'cmdPreview' },
  { name: 'data', desc: 'cmdData' },
  { name: 'activity', desc: 'cmdActivity' },
  { name: 'report', desc: 'cmdReport' },
  { name: 'agents', desc: 'cmdAgents' },
  { name: 'init', desc: 'cmdInit' },
  { name: 'mcp', desc: 'cmdMcp' },
  { name: 'skills', desc: 'cmdSkills' },
  { name: 'mods', desc: 'cmdMods' },
  { name: 'settings', desc: 'cmdSettings' },
  { name: 'help', desc: 'cmdHelp' },
];

const HISTORY_KEY = 'orch:prompt-history';
const readHistory = (): string[] => {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
  } catch {
    return [];
  }
};
const pushHistory = (text: string) => {
  try {
    const h = readHistory().filter((x) => x !== text);
    localStorage.setItem(HISTORY_KEY, JSON.stringify([...h, text].slice(-100)));
  } catch {
    /* storage unavailable */
  }
};
const draftKey = (sid: string | null) => `orch:draft:${sid || 'new'}`;

type Item = { key: string; label: string; hint?: string; apply: () => void };

export function Composer({ sessionId, agent, onAgentChange, busy, onSend, onAbort, onCommand, model, answering, inject,
  queued, onRemoveQueued, contextTokens, contextWindow, totalTokens, compactions, goal, mode, onModeChange, onShell, onClearGoal,
  serverCommands = [], footerExtra }: {
  sessionId: string | null;
  agent: AgentMode;
  onAgentChange: (a: AgentMode) => void;
  busy: boolean;
  onSend: (text: string, agent: AgentMode) => void;
  onAbort: () => void;
  onCommand: (name: string, arg?: string) => void;
  model: string;
  answering: boolean;
  inject: { text: string; nonce: number } | null;
  queued: string[];
  onRemoveQueued: (i: number) => void;
  contextTokens: number;
  contextWindow: number;
  totalTokens: number;
  compactions: number;
  goal: Goal | null | undefined;
  mode: PermissionMode;
  onModeChange: (mode: PermissionMode) => void;
  onShell: (command: string) => void;
  onClearGoal: () => void;
  serverCommands?: CommandInfo[];
  footerExtra?: ReactNode;
}) {
  const t = useT();
  const [text, setText] = useState('');
  const [files, setFiles] = useState<string[]>([]);
  const [sel, setSel] = useState(0);
  const [closedFor, setClosedFor] = useState<string | null>(null);
  const [histIdx, setHistIdx] = useState<number | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);

  // Per-session drafts survive switching sessions and reloads.
  useEffect(() => {
    try {
      setText(localStorage.getItem(draftKey(sessionId)) || '');
    } catch {
      setText('');
    }
    setHistIdx(null);
    ref.current?.focus();
  }, [sessionId]);
  useEffect(() => {
    try {
      if (text) localStorage.setItem(draftKey(sessionId), text);
      else localStorage.removeItem(draftKey(sessionId));
    } catch {
      /* storage unavailable */
    }
  }, [text, sessionId]);

  useEffect(() => {
    if (inject) {
      setText(inject.text);
      requestAnimationFrame(() => ref.current?.focus());
    }
  }, [inject]);

  const shellMode = text.startsWith('!');
  const mention = /(^|\s)@([\w./-]*)$/.exec(text);
  const slash = /^\/([\w-]*)$/.exec(text);

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
      const local: Item[] = COMMANDS.filter((c) => c.name.startsWith(slash[1])).map((c) => ({
        key: c.name,
        label: `/${c.name}${c.arg ? ' <…>' : ''}`,
        hint: t[c.desc],
        apply: () => {
          if (c.arg) setText(`/${c.name} `);
          else {
            setText('');
            onCommand(c.name);
          }
        },
      }));
      // Commands run by the server: /mod, /reverse, mod commands (/commit...) and skills (/code-review...).
      const remote: Item[] = serverCommands
        .filter((c) => c.name.startsWith(slash[1]) && !COMMANDS.some((x) => x.name === c.name))
        .map((c) => ({
          key: `srv-${c.name}`,
          label: `/${c.name}${c.argumentHint ? ` ${c.argumentHint}` : ''}`,
          hint: `${c.source === 'app' ? '' : `[${c.source.replace(/^skill:.*/, 'skill').replace(/^mod:/, 'mod ')}] `}${c.description}`,
          apply: () => {
            if (c.argumentHint) setText(`/${c.name} `);
            else {
              setText('');
              pushHistory(`/${c.name}`);
              onSend(`/${c.name}`, agent);
            }
          },
        }));
      return [...local, ...remote];
    }
    if (mention) {
      return files.slice(0, 12).map((f) => ({
        key: f,
        label: `@${f}`,
        apply: () => setText(text.slice(0, text.length - mention[2].length - 1) + `@${f} `),
      }));
    }
    return [];
  }, [text, files, closedFor, serverCommands]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => setSel(0), [items.length]);

  const submit = () => {
    const value = text.trim();
    if (!value) return;
    if (value.startsWith('!')) {
      if (!value.slice(1).trim()) return;
      setText('');
      setHistIdx(null);
      pushHistory(value);
      onShell(value.slice(1).trim());
      return;
    }
    const cmd = /^\/([\w-]+)(?:\s+([\s\S]*))?$/.exec(value);
    if (cmd && COMMANDS.some((c) => c.name === cmd[1]) && cmd[1] !== 'flow') {
      setText('');
      pushHistory(value);
      onCommand(cmd[1], cmd[2]?.trim());
      return;
    }
    setText('');
    setHistIdx(null);
    pushHistory(value);
    onSend(value, value.startsWith('/flow') ? 'flow' : agent);
  };

  const browseHistory = (dir: -1 | 1): boolean => {
    const h = readHistory();
    if (!h.length) return false;
    const next = histIdx === null ? (dir === -1 ? h.length - 1 : null) : histIdx + dir;
    if (next === null) return false;
    if (next < 0) return true;
    if (next >= h.length) {
      setHistIdx(null);
      setText('');
      return true;
    }
    setHistIdx(next);
    setText(h[next]);
    return true;
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (items.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => (s + 1) % items.length); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => (s - 1 + items.length) % items.length); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); items[sel]?.apply(); return; }
      if (e.key === 'Escape') { e.preventDefault(); setClosedFor(text); return; }
    }
    const el = e.currentTarget;
    if (e.key === 'ArrowUp' && el.selectionStart === 0 && el.selectionEnd === 0 && (!text || histIdx !== null)) {
      if (browseHistory(-1)) { e.preventDefault(); return; }
    }
    if (e.key === 'ArrowDown' && histIdx !== null && el.selectionStart === text.length) {
      if (browseHistory(1)) { e.preventDefault(); return; }
    }
    if (e.key === 'Tab' && e.shiftKey) {
      e.preventDefault();
      onModeChange(MODE_ORDER[(MODE_ORDER.indexOf(mode) + 1) % MODE_ORDER.length]);
      return;
    }
    if (e.key === 'Escape' && shellMode && !busy) { e.preventDefault(); setText(''); return; }
    if (e.key === 'Tab' && !e.shiftKey) {
      e.preventDefault();
      onAgentChange(AGENT_ORDER[(AGENT_ORDER.indexOf(agent) + 1) % AGENT_ORDER.length]);
      return;
    }
    if (e.key === 'Escape' && busy) { e.preventDefault(); onAbort(); return; }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  };

  const color = shellMode ? 'var(--warn)' : agentColor(agent);
  const agentLabel = agent === 'build' ? t.agentBuild : agent === 'plan' ? t.agentPlan : t.agentFlow;
  const placeholder = answering ? t.placeholderAnswer : agent === 'plan' ? t.placeholderPlan : agent === 'flow' ? t.placeholderFlow : t.placeholderBuild;
  const modeLabel = mode === 'auto' ? t.modeAuto : mode === 'acceptEdits' ? t.modeAcceptEdits : t.modeAsk;
  const modeHint = `${mode === 'auto' ? t.modeAutoHint : mode === 'acceptEdits' ? t.modeAcceptEditsHint : t.modeAskHint} ${t.modeCycle}.`;
  const ModeIcon = mode === 'auto' ? Bot : mode === 'acceptEdits' ? Zap : ShieldCheck;
  const ctxRatio = contextWindow ? Math.min(1, contextTokens / contextWindow) : 0;
  const ctxColor = ctxRatio > 0.75 ? 'var(--err)' : ctxRatio > 0.5 ? 'var(--warn)' : 'var(--ok)';

  return (
    <div className="relative">
      {items.length > 0 && (
        <ul className="absolute bottom-full left-0 right-0 z-20 mb-1.5 max-h-72 overflow-y-auto rounded-md border border-line bg-elev py-1 shadow-2xl" role="listbox">
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
      {queued.length > 0 && (
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="flex items-center gap-1 text-faint"><ListPlus size={12} /> {t.queued} ({t.queueHint})</span>
          {queued.map((q, i) => (
            <span key={i} className="flex max-w-[260px] items-center gap-1 rounded border border-line bg-panel px-1.5 py-0.5 text-muted">
              <span className="truncate">{q}</span>
              <button type="button" onClick={() => onRemoveQueued(i)} title={t.removeQueued} className="text-faint hover:text-err"><X size={11} /></button>
            </span>
          ))}
        </div>
      )}
      <div className="rounded-md border bg-panel transition-colors focus-within:bg-elev" style={{ borderColor: `color-mix(in srgb, ${color} 55%, var(--line))` }}>
        <div className="flex items-end gap-2 px-3 pt-2.5">
          <span className="select-none pb-1 font-bold" style={{ color }} title={shellMode ? t.shellModeHint : undefined}>{shellMode ? '!' : '›'}</span>
          <textarea
            ref={ref}
            rows={1}
            value={text}
            onChange={(e) => { setText(e.target.value); setHistIdx(null); }}
            onKeyDown={onKeyDown}
            placeholder={shellMode ? t.shellPlaceholder : placeholder}
            aria-label={shellMode ? t.shellPlaceholder : placeholder}
            className="max-h-[240px] min-h-[24px] flex-1 resize-none bg-transparent py-0.5 text-fg outline-none placeholder:text-faint focus-visible:outline-none"
          />
          {busy && !text.trim() && !shellMode ? (
            <button type="button" onClick={onAbort} title={t.stop} className="mb-0.5 rounded bg-err/80 p-1.5 text-bg hover:bg-err">
              <Square size={13} fill="currentColor" />
            </button>
          ) : (
            <button type="button" onClick={submit} disabled={!text.trim()} title={busy ? t.queued : t.send} className="mb-0.5 rounded p-1.5 text-bg disabled:opacity-30" style={{ background: color }}>
              {busy ? <ListPlus size={14} strokeWidth={2.5} /> : <ArrowUp size={14} strokeWidth={2.5} />}
            </button>
          )}
        </div>
        <div className="flex min-w-0 items-center gap-2.5 px-3 pb-2 pt-1.5 text-[11px]">
          <button
            type="button"
            onClick={() => onAgentChange(AGENT_ORDER[(AGENT_ORDER.indexOf(agent) + 1) % AGENT_ORDER.length])}
            className="shrink-0 rounded px-1.5 font-bold uppercase tracking-wider"
            style={{ color: 'var(--bg)', background: color }}
          >
            {agentLabel}
          </button>
          <span className="min-w-0 truncate text-muted">{model}</span>
          {goal && (
            <span className={cx('flex shrink-0 items-center gap-1 rounded px-1', goal.status === 'active' ? 'bg-accent/15 text-accent' : goal.status === 'achieved' ? 'text-ok' : 'text-warn')} title={`${goal.condition}\n${goal.reason}`}>
              <Target size={11} />
              {goal.status === 'active' ? t.goalActive : goal.status === 'achieved' ? t.goalAchieved : t.goalPaused}
              <button type="button" onClick={onClearGoal} title={t.clearGoal} className="opacity-60 hover:opacity-100"><X size={10} /></button>
            </span>
          )}
          {shellMode ? (
            <span className="flex shrink-0 items-center gap-1 rounded border border-warn/60 bg-warn/10 px-1 text-warn" title={t.shellModeHint}>
              <SquareTerminal size={11} /> shell
            </span>
          ) : null}
          <button type="button" onClick={() => onModeChange(MODE_ORDER[(MODE_ORDER.indexOf(mode) + 1) % MODE_ORDER.length])} title={modeHint}
            aria-label={`Permission mode: ${modeLabel}`}
            className={cx('flex shrink-0 items-center gap-1 rounded border px-1', mode === 'auto' ? 'border-warn/60 bg-warn/10 text-warn'
              : mode === 'acceptEdits' ? 'border-accent/60 bg-accent/10 text-accent' : 'border-line text-faint hover:text-fg')}>
            <ModeIcon size={11} /> {modeLabel}
          </button>
          {footerExtra}
          {busy && <span className="pulse shrink-0 text-accent">{t.working}</span>}
          <span className="ml-auto hidden shrink-0 items-center gap-2.5 text-faint md:flex">
            {contextWindow > 0 && contextTokens > 0 && (
              <span className="flex items-center gap-1" title={`${fmtTokens(contextTokens)} / ${fmtTokens(contextWindow)}`}>
                {t.context}
                <span className="inline-block h-1.5 w-12 overflow-hidden rounded-full bg-line">
                  <span className="block h-full" style={{ width: `${ctxRatio * 100}%`, background: ctxColor }} />
                </span>
                {Math.round(ctxRatio * 100)}%
              </span>
            )}
            {totalTokens > 0 && <span className="tabular-nums">{fmtTokens(totalTokens)} tok</span>}
            {compactions > 0 && <span>{compactions} {t.compactions}</span>}
            <span>{t.hintTab}</span><span>{t.hintCmd}</span><span>{t.hintFile}</span>
          </span>
        </div>
      </div>
    </div>
  );
}
