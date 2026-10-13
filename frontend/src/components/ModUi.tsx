import { useEffect } from 'react';
import { CheckCircle2, Info, Puzzle, Settings2, TriangleAlert, X, XCircle } from 'lucide-react';
import type { ModToast, ModUi } from '../types';
import { useT } from '../i18n';
import { cx } from '../util';
import { Markdown } from './Markdown';

/* Bands mods show above the prompt (e.g. "3 uncommitted changes - Commit"). */
export function ModBand({ ui, onRun, disabled }: { ui?: ModUi; onRun: (command: string) => void; disabled?: boolean }) {
  const bands = Object.entries(ui?.band || {});
  if (!bands.length) return null;
  return (
    <div className="space-y-1">
      {bands.map(([mod, b]) => (
        <div key={mod} className="flex min-w-0 items-center gap-2 rounded-md border border-accent/30 bg-accent/5 px-3 py-1.5 text-[12px]">
          <Puzzle size={12} className="shrink-0 text-accent" />
          <div className="min-w-0 flex-1 [&_.md>*+*]:mt-0"><Markdown text={b.markdown} /></div>
          {b.actions.map((a) => (
            <button key={a.command} type="button" disabled={disabled} onClick={() => onRun(a.command)}
              className="shrink-0 rounded bg-accent px-2 py-0.5 text-[11px] font-semibold text-bg hover:brightness-110 disabled:opacity-40">{a.label}</button>
          ))}
        </div>
      ))}
    </div>
  );
}

/* The status line under the prompt, fed by mods (statusline, diagnostics, your own). */
export function ModStatusLine({ ui }: { ui?: ModUi }) {
  const items = Object.entries(ui?.status || {});
  if (!items.length) return null;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 px-1 text-[11px] text-faint" aria-label="status line">
      {items.map(([mod, text], i) => (
        <span key={mod} title={`mod: ${mod}`} className={cx('min-w-0 truncate', text.startsWith('⚠') && 'text-warn', i > 0 && 'border-l border-line pl-3')}>{text}</span>
      ))}
    </div>
  );
}

/* Side panel tab: the sections mods publish (diagnostics, blocked actions...). */
export function ModPanels({ ui, onManage }: { ui?: ModUi; onManage: () => void }) {
  const t = useT();
  const panels = Object.entries(ui?.panels || {});
  return (
    <div className="space-y-3 p-3">
      <button type="button" onClick={onManage} className="inline-flex items-center gap-1 rounded border border-line px-2 py-1 text-[12px] text-muted hover:text-fg">
        <Settings2 size={12} /> {t.manageMods}
      </button>
      {!panels.length && <div className="text-[12px] text-faint">{t.modPanelsEmpty}</div>}
      {panels.map(([mod, p]) => (
        <section key={mod} className="rounded-md border border-line bg-panel px-3 py-2 text-[12px]">
          <div className="mb-1 flex items-center gap-1.5 font-semibold"><Puzzle size={12} className="text-accent" /> {p.title}</div>
          <Markdown text={p.markdown} />
        </section>
      ))}
    </div>
  );
}

const TOAST_ICON = {
  info: <Info size={13} className="text-accent" />,
  success: <CheckCircle2 size={13} className="text-ok" />,
  warning: <TriangleAlert size={13} className="text-warn" />,
  error: <XCircle size={13} className="text-err" />,
};

export function Toasts({ toasts, onDismiss }: { toasts: ModToast[]; onDismiss: (id: number) => void }) {
  useEffect(() => {
    if (!toasts.length) return;
    const id = setTimeout(() => onDismiss(toasts[0].id), 6000);
    return () => clearTimeout(id);
  }, [toasts, onDismiss]);
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed right-3 top-3 z-[60] flex w-[min(360px,90vw)] flex-col gap-1.5" role="status">
      {toasts.slice(-4).map((x) => (
        <div key={x.id} className="toast-in pointer-events-auto flex items-start gap-2 rounded-md border border-line bg-elev px-3 py-2 text-[12px] shadow-xl">
          {TOAST_ICON[x.kind] || TOAST_ICON.info}
          <div className="min-w-0 flex-1"><span className="text-faint">{x.mod}</span> <span className="break-words">{x.text}</span></div>
          <button type="button" onClick={() => onDismiss(x.id)} className="text-faint hover:text-fg" aria-label="Dismiss"><X size={12} /></button>
        </div>
      ))}
    </div>
  );
}
