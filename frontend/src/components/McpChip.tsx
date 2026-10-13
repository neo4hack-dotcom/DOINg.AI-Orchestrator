import { useCallback, useEffect, useRef, useState } from 'react';
import { Plug, Settings2 } from 'lucide-react';
import type { McpOverview, McpServer } from '../types';
import { api } from '../api';
import { useT } from '../i18n';
import { cx } from '../util';

/* Composer chip: the MCP servers of this session - switch them per session, approve project servers, open settings. */
export function McpChip({ sessionId, directory, disabled, onChange, onManage, version }: {
  sessionId: string | null; directory: string; disabled: string[]; onChange: (list: string[]) => void; onManage: () => void; version: number;
}) {
  const t = useT();
  const [data, setData] = useState<McpOverview | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const load = useCallback(() => api.mcp(directory).then(setData).catch(() => {}), [directory]);
  useEffect(() => { load(); }, [load, version]);
  useEffect(() => {
    if (!open) return;
    load();
    const onDown = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [open, load]);

  if (!data) return null;
  const servers: McpServer[] = [...data.servers.filter((s) => s.enabled !== false), ...data.project.filter((s) => s.approved)];
  const pending = data.project.filter((s) => !s.approved);
  if (!servers.length && !pending.length) return null;
  const active = servers.filter((s) => !disabled.includes(s.name));
  const failing = active.some((s) => s.live?.status === 'error');

  const toggle = (name: string) => {
    const next = disabled.includes(name) ? disabled.filter((n) => n !== name) : [...disabled, name];
    onChange(next);
    if (sessionId) api.patchSession(sessionId, { mcpDisabled: next }).catch(() => {});
  };

  return (
    <div ref={box} className="relative shrink-0">
      <button type="button" onClick={() => setOpen((o) => !o)} title={t.mcpServers}
        className={cx('flex items-center gap-1 rounded border px-1', pending.length ? 'border-warn/60 text-warn' : failing ? 'border-err/50 text-err' : 'border-line text-faint hover:text-fg')}>
        <Plug size={11} /> MCP {active.length}{pending.length ? ` · ${pending.length}?` : ''}
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-30 mb-2 w-80 rounded-md border border-line bg-elev p-2 text-[12px] shadow-2xl">
          <div className="mb-1 font-semibold">{t.mcpServers}</div>
          {servers.map((s) => (
            <label key={s.name} className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-panel">
              <input type="checkbox" className="accent-[var(--accent)]" checked={!disabled.includes(s.name)} onChange={() => toggle(s.name)} disabled={!sessionId} />
              <span className={cx('h-1.5 w-1.5 rounded-full', s.live?.status === 'connected' ? 'bg-ok' : s.live?.status === 'error' ? 'bg-err' : 'bg-faint')} />
              <span className="font-semibold">{s.name}</span>
              <span className="min-w-0 truncate text-[11px] text-faint">{s.live?.tools ? `${s.live.tools} tools` : s.description || s.type}</span>
            </label>
          ))}
          {pending.length > 0 && (
            <div className="mt-1.5 rounded border border-warn/40 bg-warn/5 p-1.5">
              <div className="mb-1 text-[11px] text-warn">{t.mcpProjectPending}</div>
              {pending.map((s) => (
                <div key={s.name} className="flex items-center gap-2">
                  <span className="font-semibold">{s.name}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-faint">{s.url || [s.command, ...(s.args || [])].join(' ')}</span>
                  <button type="button" onClick={() => api.approveMcp(directory, s.name, true).then(load)} className="rounded bg-warn px-1.5 text-[11px] font-semibold text-bg">{t.approveServer}</button>
                </div>
              ))}
            </div>
          )}
          <button type="button" onClick={() => { setOpen(false); onManage(); }} className="mt-2 inline-flex items-center gap-1 text-[11px] text-muted hover:text-fg">
            <Settings2 size={11} /> {t.mcpManage}
          </button>
        </div>
      )}
    </div>
  );
}
