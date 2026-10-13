import { ShieldAlert } from 'lucide-react';
import type { Permission } from '../types';
import { useT } from '../i18n';
import { TierIcon } from './TierIcon';

const CATEGORY: Record<string, string> = {
  edit: 'Edit a file',
  bash: 'Run a command',
  webfetch: 'Fetch a URL',
  mcp: 'Call an MCP tool',
  mods: 'Install a mod',
  open: 'Open with the system',
  data: 'Modify a database',
};

/* What the agent is about to do, shown the way Claude Code shows it: the command, the edit as a diff, the new
   file's content, the SQL. */
function Preview({ p }: { p: Permission }) {
  const i = p.input || {};
  const box = 'mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-bg/60 px-2 py-1 font-mono text-[12px] text-fg';
  if (p.tool === 'bash') return <pre className={box}>$ {i.command || ''}{i.run_in_background ? '   (background)' : ''}</pre>;
  if (p.tool === 'edit' && typeof i.old_string === 'string') {
    const lines = (s: string, sign: string, cls: string) => s.split('\n').slice(0, 40).map((l, k) => <div key={sign + k} className={cls}>{sign} {l}</div>);
    return (
      <div className={box}>
        <div className="mb-1 text-muted">{i.path}{i.replace_all ? ' (all occurrences)' : ''}</div>
        {lines(i.old_string, '-', 'bg-err/10 text-err')}
        {lines(i.new_string || '', '+', 'bg-ok/10 text-ok')}
      </div>
    );
  }
  if (p.tool === 'multiedit' && Array.isArray(i.edits)) {
    return (
      <div className={box}>
        <div className="mb-1 text-muted">{i.path} · {i.edits.length} edits</div>
        {i.edits.slice(0, 6).map((e: any, k: number) => (
          <div key={k} className="mb-1 border-l border-line pl-2">
            {String(e.old_string || '').split('\n').slice(0, 8).map((l, n) => <div key={`o${n}`} className="bg-err/10 text-err">- {l}</div>)}
            {String(e.new_string || '').split('\n').slice(0, 8).map((l, n) => <div key={`n${n}`} className="bg-ok/10 text-ok">+ {l}</div>)}
          </div>
        ))}
      </div>
    );
  }
  if (p.tool === 'write') {
    const content = String(i.content ?? '');
    const lines = content.split('\n');
    return (
      <div className={box}>
        <div className="mb-1 text-muted">{i.path} · {lines.length} lines</div>
        {lines.slice(0, 30).map((l, k) => <div key={k} className="text-ok">+ {l}</div>)}
        {lines.length > 30 && <div className="text-faint">… {lines.length - 30} more lines</div>}
      </div>
    );
  }
  if (p.tool === 'apply_patch') return <pre className={box}>{String(i.patch || '').slice(0, 4000)}</pre>;
  if (p.tool === 'data_query') return <pre className={box}><span className="text-muted">{i.source} · </span>{i.sql}</pre>;
  if (p.tool === 'open') return <pre className={box}>{i.target || i.path || i.url}</pre>;
  if (p.tool === 'webfetch') return <pre className={box}>{i.url}</pre>;
  return <pre className={box}>{p.tool} {JSON.stringify(i, null, 1).slice(0, 600)}</pre>;
}

export function PermissionBar({ permissions, onReply }: { permissions: Permission[]; onReply: (id: string, r: 'once' | 'always' | 'reject') => void }) {
  const t = useT();
  if (!permissions.length) return null;
  const p = permissions[0];
  const tier = p.agent.startsWith('savvy-') ? p.agent.slice(6) : undefined;
  return (
    <div className="rounded-md border border-warn/50 bg-warn/5 px-3 py-2" role="alertdialog" aria-label={t.permissionTitle}>
      <div className="flex min-w-0 items-center gap-2">
        <ShieldAlert size={14} className="shrink-0 text-warn" />
        <span className="shrink-0 font-semibold text-warn">{t.permissionTitle}</span>
        <span className="shrink-0 text-[12px] text-fg">{CATEGORY[p.category] || p.category}</span>
        <span className="flex min-w-0 items-center gap-1 text-[12px] text-muted">
          {tier && <TierIcon tier={tier} size={13} />} {p.agent}
        </span>
        {permissions.length > 1 && <span className="ml-auto shrink-0 text-[11px] text-faint">+{permissions.length - 1}</span>}
      </div>
      {p.note && <div className="mt-1 text-[11px] text-warn/90">{t.permissionWhy}: {p.note}</div>}
      <Preview p={p} />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" autoFocus onClick={() => onReply(p.id, 'once')} className="rounded bg-warn px-2.5 py-1 text-[12px] font-semibold text-bg hover:brightness-110">{t.allowOnce}</button>
        <button type="button" onClick={() => onReply(p.id, 'always')} className="rounded border border-warn/60 px-2.5 py-1 text-[12px] text-warn hover:bg-warn/10">{t.allowAlways}</button>
        <button type="button" onClick={() => onReply(p.id, 'reject')} className="rounded border border-line px-2.5 py-1 text-[12px] text-muted hover:text-err">{t.reject}</button>
      </div>
    </div>
  );
}
