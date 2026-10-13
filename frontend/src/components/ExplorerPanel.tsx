import { useCallback, useEffect, useState } from 'react';
import { ChevronRight, Eye, EyeOff, File, FilePlus, Folder, FolderOpen, FolderPlus, RefreshCw } from 'lucide-react';
import type { TreeEntry } from '../types';
import { api } from '../api';
import { cx } from '../util';
import { FilesPanel } from './FilesPanel';

function Node({ sessionId, entry, depth, open, onOpenFile, version, hidden, selected }: {
  sessionId: string; entry: TreeEntry; depth: number; open: (p: string) => boolean; onOpenFile: (path: string) => void;
  version: number; hidden: boolean; selected: string | null;
}) {
  const [expanded, setExpanded] = useState(open(entry.path));
  const [children, setChildren] = useState<TreeEntry[] | null>(null);
  useEffect(() => {
    if (!entry.dir || !expanded) return;
    api.tree(sessionId, entry.path, hidden).then((r) => setChildren(r.entries)).catch(() => setChildren([]));
  }, [entry.dir, entry.path, expanded, sessionId, version, hidden]);
  const Icon = entry.dir ? (expanded ? FolderOpen : Folder) : File;
  return (
    <li>
      <button type="button" onClick={() => (entry.dir ? setExpanded((x) => !x) : onOpenFile(entry.path))}
        className={cx('flex w-full min-w-0 items-center gap-1 rounded py-0.5 pr-1 text-left text-[12px] hover:bg-elev',
          entry.ignored && 'opacity-50', selected === entry.path && 'bg-accent/10 text-fg')}
        style={{ paddingLeft: 4 + depth * 12 }} title={entry.path}>
        {entry.dir ? <ChevronRight size={11} className={cx('shrink-0 text-faint transition-transform', expanded && 'rotate-90')} /> : <span className="w-[11px] shrink-0" />}
        <Icon size={12} className={cx('shrink-0', entry.dir ? 'text-accent/80' : 'text-faint')} />
        <span className="truncate">{entry.name}</span>
        {!entry.dir && <span className="ml-auto shrink-0 text-[10px] text-faint">{entry.size >= 1024 ? `${Math.round(entry.size / 1024)}k` : entry.size}</span>}
      </button>
      {entry.dir && expanded && children && (
        <ul>{children.map((c) => <Node key={c.path} sessionId={sessionId} entry={c} depth={depth + 1} open={open} onOpenFile={onOpenFile}
          version={version} hidden={hidden} selected={selected} />)}</ul>
      )}
    </li>
  );
}

/* Files tab: the project tree (open, preview, edit, create) and the changes made in this session. */
export function ExplorerPanel({ sessionId, busy, version, onOpenFile, selected }: {
  sessionId: string | null; busy: boolean; version: number; onOpenFile: (path: string) => void; selected: string | null;
}) {
  const [mode, setMode] = useState<'tree' | 'changes'>('tree');
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [hidden, setHidden] = useState(false);
  const [local, setLocal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    if (!sessionId) return;
    api.tree(sessionId, '', hidden).then((r) => { setEntries(r.entries); setError(null); }).catch((e) => setError(e.message));
  }, [sessionId, hidden]);
  useEffect(load, [load, version, local]);

  const create = async (dir: boolean) => {
    if (!sessionId) return;
    const path = window.prompt(dir ? 'New folder (path in the project)' : 'New file (path in the project)');
    if (!path) return;
    try {
      if (dir) await api.mkdir(sessionId, path);
      else {
        await api.writeFile(sessionId, path, '', null);
        onOpenFile(path);
      }
      setLocal((v) => v + 1);
    } catch (e: any) {
      setError(e.message);
    }
  };

  if (!sessionId) return <div className="p-3 text-[12px] text-faint">Open a session to browse its files.</div>;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-line px-2 py-1 text-[11px]">
        {(['tree', 'changes'] as const).map((m) => (
          <button key={m} type="button" onClick={() => setMode(m)}
            className={cx('rounded px-2 py-0.5', mode === m ? 'bg-accent/15 text-accent' : 'text-muted hover:text-fg')}>
            {m === 'tree' ? 'Explorer' : 'Changes'}
          </button>
        ))}
        {mode === 'tree' && (
          <span className="ml-auto flex gap-0.5">
            <button type="button" title="New file" onClick={() => create(false)} className="rounded p-1 text-faint hover:text-fg"><FilePlus size={12} /></button>
            <button type="button" title="New folder" onClick={() => create(true)} className="rounded p-1 text-faint hover:text-fg"><FolderPlus size={12} /></button>
            <button type="button" title={hidden ? 'Hide dot files' : 'Show dot files'} onClick={() => setHidden((h) => !h)} className="rounded p-1 text-faint hover:text-fg">
              {hidden ? <EyeOff size={12} /> : <Eye size={12} />}
            </button>
            <button type="button" title="Refresh" onClick={() => setLocal((v) => v + 1)} className="rounded p-1 text-faint hover:text-fg"><RefreshCw size={12} /></button>
          </span>
        )}
      </div>
      {mode === 'changes' ? (
        <div className="min-h-0 flex-1 overflow-y-auto"><FilesPanel sessionId={sessionId} busy={busy} version={version} /></div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-1">
          {error && <p className="px-2 text-[11px] text-err">{error}</p>}
          <ul>{entries.map((e) => <Node key={e.path} sessionId={sessionId} entry={e} depth={0} open={() => false} onOpenFile={onOpenFile}
            version={version + local} hidden={hidden} selected={selected} />)}</ul>
        </div>
      )}
    </div>
  );
}
