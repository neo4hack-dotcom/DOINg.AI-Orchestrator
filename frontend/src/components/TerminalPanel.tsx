import { useEffect, useMemo, useRef, useState } from 'react';
import { Bot, CornerDownLeft, Eye, Square, SquareTerminal, Trash2, X } from 'lucide-react';
import type { ProcInfo } from '../types';
import { api } from '../api';
import { cx } from '../util';

const ANSI = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07/g;
export const stripAnsi = (s: string) => s.replace(ANSI, '');

const HISTORY_KEY = 'orch:terminal-history';
const readHistory = (): string[] => {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
  } catch {
    return [];
  }
};

/* Integrated terminal: commands run in the session folder (cd is remembered), output streams live,
   running programs get stdin and Ctrl+C, background processes of the agents show up here too. */
export function TerminalPanel({ sessionId, procs, cwd, selected, onSelect, onClose, onPreview, height }: {
  sessionId: string;
  procs: ProcInfo[];
  cwd: string;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onClose: () => void;
  onPreview: (url: string) => void;
  height: number;
}) {
  const [command, setCommand] = useState('');
  const [stdin, setStdin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [histIdx, setHistIdx] = useState<number | null>(null);
  const out = useRef<HTMLPreElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const ordered = useMemo(() => [...procs].sort((a, b) => a.startedAt - b.startedAt), [procs]);
  const current = ordered.find((p) => p.id === selected) || ordered[ordered.length - 1] || null;

  useEffect(() => {
    const el = out.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [current?.output, current?.id]);
  useEffect(() => { input.current?.focus(); }, []);

  const run = async () => {
    const cmd = command.trim();
    if (!cmd) return;
    setError(null);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify([...readHistory().filter((h) => h !== cmd), cmd].slice(-100)));
    } catch {
      /* storage unavailable */
    }
    setCommand('');
    setHistIdx(null);
    try {
      const r = await api.terminalRun(sessionId, cmd);
      if (r.process) onSelect(r.process.id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const sendInput = async () => {
    if (!current || current.status !== 'running') return;
    const text = stdin;
    setStdin('');
    await api.terminalInput(sessionId, current.id, text).catch((e) => setError(e.message));
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') { e.preventDefault(); run(); return; }
    if (e.key === 'c' && e.ctrlKey && current?.status === 'running' && !command) { e.preventDefault(); api.terminalKill(sessionId, current.id); return; }
    const h = readHistory();
    if (e.key === 'ArrowUp' && h.length) {
      e.preventDefault();
      const i = histIdx === null ? h.length - 1 : Math.max(0, histIdx - 1);
      setHistIdx(i);
      setCommand(h[i]);
    } else if (e.key === 'ArrowDown' && histIdx !== null) {
      e.preventDefault();
      const i = histIdx + 1;
      if (i >= h.length) { setHistIdx(null); setCommand(''); } else { setHistIdx(i); setCommand(h[i]); }
    }
  };

  const shortCwd = cwd.length > 50 ? `…${cwd.slice(-48)}` : cwd;
  return (
    <div className="flex flex-col border-t border-line bg-panel" style={{ height }}>
      <div className="flex min-w-0 items-center gap-1 border-b border-line px-2 py-1">
        <SquareTerminal size={13} className="shrink-0 text-accent" />
        <span className="mr-1 shrink-0 text-[12px] font-semibold">Terminal</span>
        <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
          {ordered.map((p) => (
            <button key={p.id} type="button" onClick={() => onSelect(p.id)} title={`${p.command}\n${p.cwd}`}
              className={cx('flex max-w-[220px] shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]',
                current?.id === p.id ? 'border-accent/60 bg-accent/10 text-fg' : 'border-line text-muted hover:text-fg')}>
              <span className={cx('h-1.5 w-1.5 shrink-0 rounded-full', p.status === 'running' ? 'pulse bg-ok' : p.exitCode === 0 ? 'bg-faint' : 'bg-err')} />
              {p.owner === 'agent' && <Bot size={10} className="shrink-0 text-flow" />}
              <span className="truncate">{p.command}</span>
            </button>
          ))}
        </div>
        <button type="button" onClick={onClose} className="shrink-0 rounded p-1 text-faint hover:text-fg" title="Close (Ctrl+`)"><X size={13} /></button>
      </div>
      {current ? (
        <div className="flex min-w-0 items-center gap-2 border-b border-line px-2 py-0.5 text-[11px] text-faint">
          <span className="min-w-0 truncate">{current.owner === 'agent' ? 'agent' : 'you'} · {current.cwd}</span>
          <span className={current.status === 'running' ? 'text-ok' : current.exitCode === 0 ? '' : 'text-err'}>
            {current.status === 'running' ? 'running' : current.status === 'killed' ? 'stopped' : `exit ${current.exitCode}`}
          </span>
          {current.urls.map((u) => (
            <button key={u} type="button" onClick={() => onPreview(u)} className="inline-flex items-center gap-0.5 rounded border border-accent/40 px-1 text-accent hover:bg-accent/10">
              <Eye size={10} /> {u.replace(/^https?:\/\//, '')}
            </button>
          ))}
          <span className="ml-auto flex gap-1">
            {current.status === 'running' && (
              <button type="button" onClick={() => api.terminalKill(sessionId, current.id)} className="inline-flex items-center gap-0.5 rounded border border-line px-1 hover:text-err">
                <Square size={9} fill="currentColor" /> stop
              </button>
            )}
            {current.status !== 'running' && (
              <button type="button" onClick={() => { api.terminalRemove(sessionId, current.id); onSelect(null); }} className="inline-flex items-center gap-0.5 rounded border border-line px-1 hover:text-fg">
                <Trash2 size={9} /> clear
              </button>
            )}
          </span>
        </div>
      ) : null}
      <pre ref={out} className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-all px-3 py-1.5 font-mono text-[12px] leading-[1.45] text-fg/90">
        {current ? stripAnsi(current.output || '') || (current.status === 'running' ? '…' : '(no output)') : 'Type a command below. It runs in the project folder; `cd` is remembered. Ctrl+C stops the selected program.'}
      </pre>
      {error && <div className="px-3 text-[11px] text-err">{error}</div>}
      <div className="flex items-center gap-2 border-t border-line px-2 py-1">
        <span className="shrink-0 font-mono text-[11px] text-faint" title={cwd}>{shortCwd} $</span>
        <input ref={input} value={command} onChange={(e) => setCommand(e.target.value)} onKeyDown={onKey} spellCheck={false}
          placeholder="command…" aria-label="Terminal command"
          className="min-w-0 flex-1 bg-transparent font-mono text-[12px] text-fg outline-none placeholder:text-faint" />
        {current?.status === 'running' && (
          <input value={stdin} onChange={(e) => setStdin(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && sendInput()}
            placeholder="input to the program ↵" aria-label="Program input"
            className="w-48 rounded border border-line bg-bg px-1.5 py-0.5 font-mono text-[11px] outline-none focus:border-accent" />
        )}
        <button type="button" onClick={run} disabled={!command.trim()} className="rounded p-1 text-faint hover:text-fg disabled:opacity-30" title="Run"><CornerDownLeft size={13} /></button>
      </div>
    </div>
  );
}
