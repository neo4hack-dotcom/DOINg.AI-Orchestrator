import { useEffect, useRef, useState } from 'react';
import { ExternalLink, Globe, Maximize2, Minimize2, Monitor, RotateCw, Smartphone, Tablet } from 'lucide-react';
import { api } from '../api';
import { cx } from '../util';

const DEVICES = { desktop: '100%', tablet: '768px', mobile: '390px' } as const;
type Device = keyof typeof DEVICES;

/* Hosts the preview may show: this machine and private networks (the app is local-only). */
export function isLocalUrl(url: string): boolean {
  try {
    const h = new URL(url).hostname.replace(/^\[|\]$/g, '');
    return h === 'localhost' || h.endsWith('.localhost') || h === '::1' || /^127\./.test(h) || /^10\./.test(h) || /^192\.168\./.test(h)
      || /^172\.(1[6-9]|2\d|3[01])\./.test(h) || /^f[cd][0-9a-f]{2}:/i.test(h) || !h.includes('.');
  } catch {
    return false;
  }
}

/* What the preview frame may embed (the page's CSP): this machine only. Private-network apps open in the browser. */
const embeddable = (url: string) => {
  try {
    const h = new URL(url).hostname;
    return h === 'localhost' || h === '127.0.0.1' || h.endsWith('.localhost');
  } catch {
    return false;
  }
};

/* Live preview of what is being built: dev servers started by you or the agents (detected URLs), any local
   URL, or an HTML page of the workspace (sandboxed). */
export function PreviewPanel({ sessionId, url, onUrl, wide, onToggleWide, version, localOnly }: {
  sessionId: string | null;
  url: string;
  onUrl: (url: string) => void;
  wide: boolean;
  onToggleWide: () => void;
  version: number;
  localOnly: boolean;
}) {
  const [targets, setTargets] = useState<{ urls: { url: string; command: string }[]; pages: string[] }>({ urls: [], pages: [] });
  const [draft, setDraft] = useState(url);
  const [device, setDevice] = useState<Device>('desktop');
  const [nonce, setNonce] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);

  useEffect(() => setDraft(url), [url]);
  useEffect(() => {
    if (!sessionId) return;
    api.previewTargets(sessionId).then((t) => {
      setTargets(t);
      if (!url && t.urls[0]) onUrl(t.urls[0].url);
      else if (!url && t.pages[0]) onUrl(`workspace:${t.pages[0]}`);
    }).catch(() => {});
  }, [sessionId, version]); // eslint-disable-line react-hooks/exhaustive-deps

  const isWorkspace = url.startsWith('workspace:');
  const src = !url ? '' : isWorkspace && sessionId ? api.rawUrl(sessionId, url.slice(10)) : url;
  const blocked = Boolean(url) && !isWorkspace && localOnly && !isLocalUrl(url);
  const go = () => {
    let u = draft.trim();
    if (!u) return;
    if (!/^(https?:|workspace:)/.test(u)) u = /^[\w.-]+:\d+/.test(u) || u.startsWith('localhost') ? `http://${u}` : `workspace:${u.replace(/^\/+/, '')}`;
    onUrl(u);
    setNonce((n) => n + 1);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-line px-2 py-1.5">
        <Globe size={13} className="shrink-0 text-faint" />
        <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && go()} spellCheck={false}
          placeholder="http://localhost:5173 or a workspace file (index.html)" aria-label="Preview URL"
          className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-0.5 font-mono text-[12px] outline-none focus:border-accent" />
        <button type="button" title="Reload" onClick={() => setNonce((n) => n + 1)} className="rounded p-1 text-faint hover:text-fg"><RotateCw size={13} /></button>
        {(['desktop', 'tablet', 'mobile'] as Device[]).map((d) => {
          const Icon = d === 'desktop' ? Monitor : d === 'tablet' ? Tablet : Smartphone;
          return (
            <button key={d} type="button" title={d} onClick={() => setDevice(d)} className={cx('rounded p-1', device === d ? 'text-accent' : 'text-faint hover:text-fg')}>
              <Icon size={13} />
            </button>
          );
        })}
        {src && !isWorkspace && (
          <a href={src} target="_blank" rel="noopener noreferrer" title="Open in the browser" className="rounded p-1 text-faint hover:text-fg"><ExternalLink size={13} /></a>
        )}
        <button type="button" title={wide ? 'Narrow panel' : 'Wide panel'} onClick={onToggleWide} className="rounded p-1 text-faint hover:text-fg">
          {wide ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
      </div>
      {(targets.urls.length > 0 || targets.pages.length > 0) && (
        <div className="flex flex-wrap gap-1 border-b border-line px-2 py-1 text-[11px]">
          {targets.urls.map((t) => (
            <button key={t.url} type="button" onClick={() => { onUrl(t.url); setNonce((n) => n + 1); }} title={t.command}
              className={cx('rounded border px-1.5', url === t.url ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}>
              ● {t.url.replace(/^https?:\/\//, '')}
            </button>
          ))}
          {targets.pages.map((p) => (
            <button key={p} type="button" onClick={() => { onUrl(`workspace:${p}`); setNonce((n) => n + 1); }}
              className={cx('rounded border px-1.5', url === `workspace:${p}` ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}>
              {p}
            </button>
          ))}
        </div>
      )}
      <div className="relative min-h-0 flex-1 overflow-auto bg-[#1a1a1f]">
        {!src ? (
          <div className="space-y-2 p-4 text-[12px] text-muted">
            <p>Nothing to preview yet.</p>
            <p>Start your app's dev server (terminal, or ask the agent: "run the dev server and show it to me"), or type a workspace HTML file.
              Detected local URLs appear above.</p>
          </div>
        ) : blocked ? (
          <div className="p-4 text-[12px] text-warn">Local-only mode: the preview only shows local or private-network addresses.</div>
        ) : !isWorkspace && !embeddable(src) ? (
          <div className="space-y-2 p-4 text-[12px] text-muted">
            <p>Only apps running on this machine (localhost) can be shown inside the app.</p>
            <a href={src} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent underline"><ExternalLink size={12} /> Open {src} in the browser</a>
          </div>
        ) : (
          <div className="mx-auto h-full bg-white transition-all" style={{ width: DEVICES[device], maxWidth: '100%' }}>
            <iframe ref={frame} key={`${src}-${nonce}`} title="Preview" src={src} className="h-full w-full border-0"
              sandbox={isWorkspace ? 'allow-scripts allow-forms allow-popups allow-modals' : 'allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads'} />
          </div>
        )}
      </div>
    </div>
  );
}
