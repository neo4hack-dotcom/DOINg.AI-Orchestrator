import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AtSign, Copy, Database, ExternalLink, Eye, FileCode2, Loader2, Pencil, RefreshCw, Save, Trash2, X } from 'lucide-react';
import type { FileData } from '../types';
import { api } from '../api';
import { cx } from '../util';
import { Markdown } from './Markdown';

const IMAGE = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i;
const DATA = /\.(csv|tsv|xlsx|jsonl|ndjson)$/i;
const ext = (p: string) => (/\.([^.\\/]+)$/.exec(p)?.[1] || '').toLowerCase();

/* A workspace file: code view with line numbers, editor (Ctrl+S), previews (Markdown, HTML, images, PDF),
   open with the system app, send to the chat, open data files in the Data view. */
export function FileViewer({ sessionId, path, line, startInPreview, onClose, onMention, onOpenData, onDeleted }: {
  sessionId: string;
  path: string;
  line?: number | null;
  startInPreview?: boolean;
  onClose: () => void;
  onMention: (path: string) => void;
  onOpenData: (path: string) => void;
  onDeleted: () => void;
}) {
  const [file, setFile] = useState<FileData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<'view' | 'edit' | 'preview'>('view');
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const lineRef = useRef<HTMLDivElement>(null);
  const e = ext(path);
  const previewable = IMAGE.test(path) || ['md', 'markdown', 'html', 'htm', 'pdf'].includes(e);

  const load = useCallback(() => {
    setError(null);
    api.readFile(sessionId, path).then((f) => {
      setFile(f);
      setDraft(f.content ?? '');
    }).catch((err) => setError(err.message));
  }, [sessionId, path]);
  useEffect(() => {
    load();
    setMode(startInPreview && (IMAGE.test(path) || ['md', 'markdown', 'html', 'htm', 'pdf'].includes(e)) ? 'preview' : IMAGE.test(path) || e === 'pdf' ? 'preview' : 'view');
  }, [load, path]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { lineRef.current?.scrollIntoView({ block: 'center' }); }, [file, line]);
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape' && mode !== 'edit') onClose();
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's' && mode === 'edit') { ev.preventDefault(); save(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!file) return;
    setSaving(true);
    try {
      const r = await api.writeFile(sessionId, path, draft, file.mtime);
      setFile({ ...file, content: draft, mtime: r.mtime });
      setMode('view');
      setNotice('Saved.');
      setTimeout(() => setNotice(null), 1500);
    } catch (err: any) {
      setError(err.status === 409 ? `${err.message}` : err.message);
    } finally {
      setSaving(false);
    }
  };

  const lines = useMemo(() => (file?.content ?? '').split('\n'), [file?.content]);
  const raw = api.rawUrl(sessionId, path);
  const dirty = mode === 'edit' && file?.content !== null && draft !== (file?.content ?? '');

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-2 sm:p-4" onMouseDown={(ev) => ev.target === ev.currentTarget && !dirty && onClose()}>
      <div role="dialog" aria-modal aria-label={path} className="flex h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-line bg-bg shadow-2xl">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
          <FileCode2 size={14} className="shrink-0 text-accent" />
          <span className="min-w-0 flex-1 truncate font-mono text-[12px]" title={path}>{path}</span>
          {file && <span className="shrink-0 text-[11px] text-faint">{file.size.toLocaleString()} B{file.crlf ? ' · CRLF' : ''}</span>}
          {notice && <span className="text-[11px] text-ok">{notice}</span>}
          {previewable && mode !== 'edit' && (
            <button type="button" onClick={() => setMode(mode === 'preview' ? 'view' : 'preview')} disabled={IMAGE.test(path) && e !== 'svg'}
              className={cx('inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[12px]', mode === 'preview' ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}>
              <Eye size={12} /> Preview
            </button>
          )}
          {file?.content !== null && file && !file.binary && (mode === 'edit' ? (
            <>
              <button type="button" onClick={save} disabled={saving || !dirty} className="inline-flex items-center gap-1 rounded bg-accent px-2 py-0.5 text-[12px] font-semibold text-bg disabled:opacity-40">
                {saving ? <Loader2 size={12} className="spin" /> : <Save size={12} />} Save
              </button>
              <button type="button" onClick={() => { setDraft(file.content ?? ''); setMode('view'); }} className="rounded border border-line px-2 py-0.5 text-[12px] text-muted hover:text-fg">Cancel</button>
            </>
          ) : (
            <button type="button" onClick={() => setMode('edit')} className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 text-[12px] text-muted hover:text-fg"><Pencil size={12} /> Edit</button>
          ))}
          {DATA.test(path) && (
            <button type="button" onClick={() => onOpenData(path)} className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 text-[12px] text-muted hover:text-fg"><Database size={12} /> Data view</button>
          )}
          <button type="button" title="Mention in the chat (@file)" onClick={() => onMention(path)} className="rounded p-1 text-faint hover:text-fg"><AtSign size={13} /></button>
          <button type="button" title="Copy path" onClick={() => navigator.clipboard?.writeText(path)} className="rounded p-1 text-faint hover:text-fg"><Copy size={13} /></button>
          <button type="button" title="Open with the system app" onClick={() => api.openExternal(sessionId, path).catch((err) => setError(err.message))}
            className="rounded p-1 text-faint hover:text-fg"><ExternalLink size={13} /></button>
          <button type="button" title="Reload" onClick={load} className="rounded p-1 text-faint hover:text-fg"><RefreshCw size={13} /></button>
          <button type="button" title="Delete" onClick={() => window.confirm(`Delete ${path}?`) && api.deleteFile(sessionId, path).then(() => { onDeleted(); onClose(); }).catch((err) => setError(err.message))}
            className="rounded p-1 text-faint hover:text-err"><Trash2 size={13} /></button>
          <button type="button" onClick={() => (!dirty || window.confirm('Discard your changes?')) && onClose()} className="rounded p-1 text-faint hover:text-fg" aria-label="Close"><X size={15} /></button>
        </div>
        {error && (
          <div className="flex items-center gap-2 border-b border-err/30 bg-err/5 px-3 py-1 text-[12px] text-err">
            <span className="min-w-0 flex-1">{error}</span>
            <button type="button" onClick={load} className="underline">reload</button>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-auto">
          {!file ? (
            <div className="flex items-center gap-2 p-4 text-muted"><Loader2 size={14} className="spin" /> Loading…</div>
          ) : mode === 'preview' ? (
            IMAGE.test(path) ? (
              <div className="grid min-h-full place-items-center bg-[repeating-conic-gradient(#ffffff0a_0_25%,transparent_0_50%)] bg-[length:16px_16px] p-4">
                <img src={raw} alt={path} className="max-h-[80vh] max-w-full" />
              </div>
            ) : e === 'md' || e === 'markdown' ? (
              <div className="mx-auto max-w-3xl px-6 py-4"><Markdown text={file.content || ''} /></div>
            ) : (
              <iframe title={path} src={raw} sandbox="allow-scripts allow-forms allow-popups allow-modals" className="h-full w-full bg-white" />
            )
          ) : mode === 'edit' ? (
            <textarea value={draft} onChange={(ev) => setDraft(ev.target.value)} spellCheck={false} autoFocus
              className="h-full w-full resize-none bg-bg px-4 py-3 font-mono text-[12.5px] leading-[1.5] text-fg outline-none" />
          ) : file.binary || file.content === null ? (
            <div className="p-4 text-[12px] text-muted">
              {file.tooLarge ? 'This file is too large to show here.' : 'Binary file.'} Use <b>Open with the system app</b>
              {previewable ? ' or Preview' : ''}.
            </div>
          ) : (
            <div className="min-w-max py-2 font-mono text-[12.5px] leading-[1.5]">
              {lines.map((l, i) => (
                <div key={i} ref={line && i + 1 === line ? lineRef : undefined} className={cx('flex', line && i + 1 === line ? 'bg-accent/15' : false)}>
                  <span className="w-12 shrink-0 select-none pr-3 text-right text-faint">{i + 1}</span>
                  <span className="whitespace-pre pr-4">{l || ' '}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
