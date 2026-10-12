import { useCallback, useEffect, useState } from 'react';
import { FileMinus, FilePen, FilePlus, RefreshCw, Undo2 } from 'lucide-react';
import type { ChangedFile } from '../types';
import { api } from '../api';
import { useT } from '../i18n';
import { cx } from '../util';
import { DiffView } from './DiffView';

/* Every file changed since the session started (shadow git snapshot), with its diff and a revert button. */
export function FilesPanel({ sessionId, busy, version }: { sessionId: string | null; busy: boolean; version: number }) {
  const t = useT();
  const [files, setFiles] = useState<ChangedFile[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [diff, setDiff] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!sessionId) return;
    api.changes(sessionId).then((r) => setFiles(r.files)).catch((e) => setError(e.message));
  }, [sessionId]);

  useEffect(load, [load, version]);
  useEffect(() => {
    if (!sessionId || !open) return;
    api.diff(sessionId, open).then(setDiff).catch(() => setDiff(''));
  }, [sessionId, open, version]);

  const revert = async (path: string) => {
    if (!sessionId || !window.confirm(t.confirmRevert)) return;
    await api.revertFile(sessionId, path).catch((e) => setError(e.message));
    load();
  };

  const add = files.reduce((a, f) => a + f.additions, 0);
  const del = files.reduce((a, f) => a + f.deletions, 0);
  return (
    <div className="p-3">
      <div className="mb-2 flex items-center gap-2 text-[11px] text-faint">
        <span>{files.length} · <span className="text-ok">+{add}</span> <span className="text-err">-{del}</span></span>
        <button type="button" onClick={load} className="ml-auto inline-flex items-center gap-1 hover:text-fg"><RefreshCw size={11} /> {t.refresh}</button>
      </div>
      {error && <p className="mb-2 text-[11px] text-err">{error}</p>}
      {!files.length && <p className="text-[12px] text-faint">{t.noChanges}</p>}
      <ul className="space-y-1">
        {files.map((f) => {
          const Icon = f.status === 'added' ? FilePlus : f.status === 'deleted' ? FileMinus : FilePen;
          return (
            <li key={f.path} className={cx('rounded border', open === f.path ? 'border-accent/50' : 'border-line')}>
              <div className="flex min-w-0 items-center gap-2 px-2 py-1">
                <button type="button" onClick={() => setOpen(open === f.path ? null : f.path)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                  <Icon size={13} className={f.status === 'added' ? 'text-ok' : f.status === 'deleted' ? 'text-err' : 'text-warn'} />
                  <span className="truncate">{f.path}</span>
                  <span className="ml-auto shrink-0 text-[11px] tabular-nums"><span className="text-ok">+{f.additions}</span> <span className="text-err">-{f.deletions}</span></span>
                </button>
                <button type="button" disabled={busy} onClick={() => revert(f.path)} title={t.revert} className="shrink-0 text-faint hover:text-warn disabled:opacity-30">
                  <Undo2 size={13} />
                </button>
              </div>
              {open === f.path && diff && <div className="px-1 pb-1"><DiffView diff={diff} /></div>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
