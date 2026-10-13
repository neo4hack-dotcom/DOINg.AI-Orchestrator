import { useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { useT } from '../i18n';
import { cx } from '../util';

export interface PaletteItem { id: string; label: string; hint?: string; group: string; run: () => void }

/* Ctrl+K: fuzzy jump to any command or session. */
export function CommandPalette({ items, onClose }: { items: PaletteItem[]; onClose: () => void }) {
  const t = useT();
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  const shown = useMemo(() => {
    const needle = q.toLowerCase().trim();
    if (!needle) return items.slice(0, 60);
    const score = (s: string) => {
      const h = s.toLowerCase();
      if (h.startsWith(needle)) return 3;
      if (h.includes(needle)) return 2;
      let i = 0;
      for (const ch of h) if (ch === needle[i]) i++;
      return i === needle.length ? 1 : 0;
    };
    return items
      .map((it) => ({ it, s: Math.max(score(it.label), score(it.hint || '') - 1) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s)
      .map((x) => x.it)
      .slice(0, 60);
  }, [q, items]);

  useEffect(() => setSel(0), [q]);
  useEffect(() => {
    listRef.current?.children[sel]?.scrollIntoView({ block: 'nearest' });
  }, [sel]);

  const run = (it?: PaletteItem) => {
    if (!it) return;
    onClose();
    it.run();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[12vh]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal aria-label={t.palette} className="w-full max-w-xl overflow-hidden rounded-lg border border-line bg-panel shadow-2xl">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
          <Search size={14} className="text-faint" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, shown.length - 1)); }
              else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
              else if (e.key === 'Enter') { e.preventDefault(); run(shown[sel]); }
              else if (e.key === 'Escape') onClose();
            }}
            placeholder={t.palettePlaceholder}
            className="flex-1 bg-transparent outline-none placeholder:text-faint"
          />
          <kbd className="text-[10px] text-faint">esc</kbd>
        </div>
        <ul ref={listRef} className="max-h-[50vh] overflow-y-auto py-1" role="listbox">
          {shown.map((it, i) => (
            <li key={it.id} role="option" aria-selected={i === sel}>
              <button type="button" onMouseEnter={() => setSel(i)} onClick={() => run(it)}
                className={cx('flex w-full min-w-0 items-baseline gap-3 px-3 py-1.5 text-left', i === sel && 'bg-accent/15')}>
                <span className="w-16 shrink-0 text-[10px] uppercase tracking-wider text-faint">{it.group}</span>
                <span className={cx('shrink-0', i === sel ? 'text-accent' : 'text-fg')}>{it.label}</span>
                {it.hint && <span className="truncate text-[12px] text-faint">{it.hint}</span>}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
