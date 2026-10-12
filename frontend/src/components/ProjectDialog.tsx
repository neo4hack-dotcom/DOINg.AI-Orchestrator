import { useEffect, useState } from 'react';
import { ArrowUp, Folder, FolderGit2, FolderPlus, HardDrive, Home, Loader2, X } from 'lucide-react';
import type { FolderListing } from '../types';
import { api } from '../api';
import { useT } from '../i18n';
import { cx } from '../util';

type Mode = 'new' | 'open';

/* Choose where a session works: open an existing folder, or create a new project folder anywhere. */
export function ProjectDialog({ defaultAuto, onClose, onStart }: {
  defaultAuto: boolean;
  onClose: () => void;
  onStart: (directory: string, autoMode: boolean) => void;
}) {
  const t = useT();
  const [mode, setMode] = useState<Mode>('new');
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [pathInput, setPathInput] = useState('');
  const [name, setName] = useState('');
  const [gitInit, setGitInit] = useState(true);
  const [auto, setAuto] = useState(defaultAuto);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const browse = (path = '') => {
    setLoading(true);
    api.listFolder(path)
      .then((l) => { setListing(l); setPathInput(l.path); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(() => browse(''), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const current = listing?.path || '';
  const sep = listing?.sep || '/';
  const target = name.trim() ? `${current.replace(/[\\/]+$/, '')}${sep}${name.trim()}` : '';

  const submit = async () => {
    setError(null);
    try {
      if (mode === 'new') {
        if (!name.trim()) return;
        const r = await api.createProject(current, name.trim(), gitInit);
        onStart(r.path, auto);
      } else {
        onStart(current, auto);
      }
    } catch (e: any) {
      setError(e.message);
    }
  };

  const crumbs = current.split(/[\\/]/).filter(Boolean);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal aria-label={t.newSession} className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-line bg-panel shadow-2xl">
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          {(['new', 'open'] as Mode[]).map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)}
              className={cx('inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[12px]', mode === m ? 'bg-accent/15 text-accent' : 'text-muted hover:text-fg')}>
              {m === 'new' ? <FolderPlus size={13} /> : <Folder size={13} />} {m === 'new' ? t.newProject : t.openFolder}
            </button>
          ))}
          <button type="button" onClick={onClose} className="ml-auto text-faint hover:text-fg" aria-label={t.close}><X size={16} /></button>
        </div>

        <div className="space-y-3 overflow-y-auto px-4 py-3">
          <label className="block space-y-1">
            <span className="text-[12px] text-muted">{mode === 'new' ? t.parentFolder : t.location}</span>
            <div className="flex gap-2">
              <input value={pathInput} onChange={(e) => setPathInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && browse(pathInput)}
                className="min-w-0 flex-1 rounded border border-line bg-bg px-2.5 py-1.5 text-[12px] outline-none focus:border-accent" spellCheck={false} />
              <button type="button" onClick={() => browse(pathInput)} className="shrink-0 rounded border border-line px-2.5 text-[12px] text-muted hover:text-fg">OK</button>
            </div>
          </label>

          <div className="flex flex-wrap items-center gap-1 text-[11px]">
            <button type="button" disabled={!listing?.parent} onClick={() => listing?.parent && browse(listing.parent)}
              className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-muted hover:text-fg disabled:opacity-30"><ArrowUp size={11} /> {t.up}</button>
            <button type="button" onClick={() => listing && browse(listing.home)} className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-muted hover:text-fg"><Home size={11} /> {t.home}</button>
            {listing && listing.roots.length > 1 && listing.roots.map((r) => (
              <button key={r} type="button" onClick={() => browse(r)} className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-muted hover:text-fg"><HardDrive size={11} /> {r}</button>
            ))}
            <span className="ml-1 min-w-0 truncate text-faint">{crumbs.join(` ${sep} `)}</span>
          </div>

          <div className="h-56 overflow-y-auto rounded border border-line bg-bg">
            {loading ? (
              <div className="p-3"><Loader2 size={14} className="spin text-faint" /></div>
            ) : listing && listing.dirs.length ? (
              <ul>
                {listing.dirs.map((d) => (
                  <li key={d.path}>
                    <button type="button" onClick={() => browse(d.path)} className="flex w-full items-center gap-2 px-2.5 py-1 text-left hover:bg-elev">
                      {d.isProject ? <FolderGit2 size={13} className="shrink-0 text-accent" /> : <Folder size={13} className="shrink-0 text-faint" />}
                      <span className="truncate">{d.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="p-3 text-[12px] text-faint">{t.noFolders}</p>
            )}
          </div>

          {listing && listing.recent.length > 0 && (
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wider text-faint">{t.recent}</div>
              <div className="flex flex-wrap gap-1.5">
                {listing.recent.slice(0, 8).map((r) => (
                  <button key={r} type="button" title={r} onClick={() => { setMode('open'); browse(r); }}
                    className="max-w-[260px] truncate rounded border border-line px-1.5 py-0.5 text-[11px] text-muted hover:text-fg">{r.split(/[\\/]/).pop() || r}</button>
                ))}
              </div>
            </div>
          )}

          {mode === 'new' && (
            <>
              <label className="block space-y-1">
                <span className="text-[12px] text-muted">{t.projectName}</span>
                <input autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()}
                  placeholder="my-app" className="w-full rounded border border-line bg-bg px-2.5 py-1.5 outline-none focus:border-accent" />
              </label>
              {target && <p className="truncate text-[11px] text-faint">{t.projectWillBe} <span className="text-muted">{target}</span></p>}
              <label className="flex items-center gap-2 text-[12px]">
                <input type="checkbox" checked={gitInit} onChange={(e) => setGitInit(e.target.checked)} className="accent-[var(--accent)]" /> {t.gitInit}
              </label>
            </>
          )}
          <label className="flex items-center gap-2 text-[12px]">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="accent-[var(--accent)]" /> {t.startAuto}
          </label>
          {error && <p className="text-[12px] text-err">{error}</p>}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button type="button" onClick={onClose} className="rounded border border-line px-3 py-1.5 text-muted hover:text-fg">{t.cancel}</button>
          <button type="button" onClick={submit} disabled={mode === 'new' ? !name.trim() : !current}
            className="rounded bg-accent px-3 py-1.5 font-semibold text-bg hover:brightness-110 disabled:opacity-40">
            {mode === 'new' ? t.createProject : t.chooseHere}
          </button>
        </div>
      </div>
    </div>
  );
}
