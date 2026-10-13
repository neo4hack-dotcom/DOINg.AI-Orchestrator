import { ShieldAlert } from 'lucide-react';
import type { Permission } from '../types';
import { useT } from '../i18n';
import { TierIcon } from './TierIcon';

function describe(p: Permission): string {
  const i = p.input || {};
  if (p.tool === 'bash') return `$ ${i.command || ''}`;
  if (p.tool === 'write' || p.tool === 'edit') return `${p.tool} ${i.path || ''}`;
  return `${p.tool} ${JSON.stringify(i).slice(0, 120)}`;
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
        <span className="flex min-w-0 items-center gap-1 text-[12px] text-muted">
          {tier && <TierIcon tier={tier} size={13} />} {p.agent}
        </span>
        {permissions.length > 1 && <span className="ml-auto shrink-0 text-[11px] text-faint">+{permissions.length - 1}</span>}
      </div>
      <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded bg-bg/60 px-2 py-1 text-[12px] text-fg">{describe(p)}</pre>
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" autoFocus onClick={() => onReply(p.id, 'once')} className="rounded bg-warn px-2.5 py-1 text-[12px] font-semibold text-bg hover:brightness-110">{t.allowOnce}</button>
        <button type="button" onClick={() => onReply(p.id, 'always')} className="rounded border border-warn/60 px-2.5 py-1 text-[12px] text-warn hover:bg-warn/10">{t.allowAlways}</button>
        <button type="button" onClick={() => onReply(p.id, 'reject')} className="rounded border border-line px-2.5 py-1 text-[12px] text-muted hover:text-err">{t.reject}</button>
      </div>
    </div>
  );
}
