import { useState } from 'react';
import { Plus, Search, Settings, Target, Trash2, Workflow } from 'lucide-react';
import type { AppConfig, SessionSummary } from '../types';
import { useT } from '../i18n';
import { agentColor, cx, fmtAgo } from '../util';

export function Sidebar({ sessions, currentId, onSelect, onNew, onDelete, onSettings, config, llmOk, lang }: {
  sessions: SessionSummary[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onSettings: () => void;
  config: AppConfig | null;
  llmOk: boolean | null;
  lang: string;
}) {
  const t = useT();
  const [q, setQ] = useState('');
  const shown = q.trim() ? sessions.filter((s) => s.title.toLowerCase().includes(q.trim().toLowerCase())) : sessions;
  return (
    <nav className="flex h-full w-full flex-col border-r border-line bg-panel" aria-label={t.sessions}>
      <div className="flex items-center gap-2 px-3 pb-2 pt-3">
        <div className="grid h-6 w-6 place-items-center rounded bg-accent font-bold text-bg">›_</div>
        <div className="min-w-0 leading-tight">
          <div className="truncate font-bold">DOINg.AI</div>
          <div className="truncate text-[11px] text-faint">Orchestrator</div>
        </div>
      </div>
      <div className="px-2 pb-2">
        <button type="button" onClick={onNew} className="flex w-full items-center gap-2 rounded border border-line px-2.5 py-1.5 text-muted hover:border-accent/60 hover:text-fg">
          <Plus size={14} /> {t.newSession}
        </button>
      </div>
      {sessions.length > 6 && (
        <div className="mx-2 mb-1 flex items-center gap-1.5 rounded border border-line px-2 py-1">
          <Search size={12} className="text-faint" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.searchSessions} aria-label={t.searchSessions}
            className="min-w-0 flex-1 bg-transparent text-[12px] outline-none placeholder:text-faint" />
        </div>
      )}
      <div className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-faint">{t.sessions}</div>
      <ul className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
        {sessions.length === 0 && <li className="px-2 py-1 text-[12px] text-faint">{t.noSessions}</li>}
        {shown.map((s) => (
          <li key={s.id} className="group relative">
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              className={cx('w-full rounded px-2 py-1.5 pr-7 text-left', s.id === currentId ? 'bg-elev text-fg' : 'text-muted hover:bg-elev/60 hover:text-fg')}
            >
              <div className="flex min-w-0 items-center gap-1.5">
                <span className={cx('h-1.5 w-1.5 shrink-0 rounded-full', s.busy && 'pulse')} style={{ background: s.busy ? 'var(--accent)' : agentColor(s.agent) }} />
                <span className="truncate">{s.title}</span>
              </div>
              <div className="flex min-w-0 items-center gap-1.5 pl-3 text-[11px] text-faint">
                {s.flowTitle && <Workflow size={10} className="shrink-0 text-flow" />}
                {s.goalActive && <Target size={10} className="shrink-0 text-accent" />}
                <span className="truncate">{fmtAgo(s.updatedAt, lang)}</span>
              </div>
            </button>
            <button
              type="button"
              onClick={() => onDelete(s.id)}
              title={t.deleteSession}
              aria-label={t.deleteSession}
              className="absolute right-1.5 top-2 hidden text-faint hover:text-err group-hover:block group-focus-within:block"
            >
              <Trash2 size={13} />
            </button>
          </li>
        ))}
      </ul>
      <div className="space-y-1.5 border-t border-line px-3 py-2.5 text-[11px]">
        {config && (
          <>
            <div className="flex min-w-0 items-center gap-1.5 text-muted" title={config.llm.baseUrl}>
              <span className={cx('h-1.5 w-1.5 shrink-0 rounded-full', llmOk === null && 'pulse')} style={{ background: llmOk ? 'var(--ok)' : llmOk === false ? 'var(--err)' : 'var(--faint)' }} />
              <span className="truncate">{config.llm.provider} · {config.llm.model}</span>
            </div>
            <div className="truncate text-faint" title={config.workspace}>{config.workspace}</div>
          </>
        )}
        <button type="button" onClick={onSettings} className="flex items-center gap-1.5 text-muted hover:text-fg">
          <Settings size={13} /> {t.settings}
        </button>
      </div>
    </nav>
  );
}
