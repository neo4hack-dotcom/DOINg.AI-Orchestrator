import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Columns3, Copy, Filter, Plus, Search, X } from 'lucide-react';
import type { DataPage, DataView, FilterOp } from '../types';
import { cx } from '../util';

export const OP_LABEL: Record<FilterOp, string> = {
  eq: '=', neq: '≠', contains: 'contains', not_contains: 'excludes', starts: 'starts with', ends: 'ends with', gt: '>', gte: '≥', lt: '<',
  lte: '≤', empty: 'is empty', not_empty: 'not empty', in: 'in (a, b)', regex: 'regex',
};
const NO_VALUE: FilterOp[] = ['empty', 'not_empty'];
const isNum = (t: string) => t === 'integer' || t === 'number';

function fmt(v: any, type: string): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number' && type === 'number') return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return String(v);
}

/* Spreadsheet-like grid over a dataset page: click a header to sort (Shift for several), filters, search,
   column picker, row selection with copy, paging. Every change goes to the server view the agent shares. */
export function DataGrid({ page, onView, onPage, busy }: {
  page: DataPage;
  onView: (view: DataView) => void;
  onPage: (offset: number) => void;
  busy: boolean;
}) {
  const view = page.dataset.view || { filters: [], sort: [], hidden: [], search: '' };
  const [search, setSearch] = useState(view.search || '');
  const [adding, setAdding] = useState<{ column: string; op: FilterOp; value: string } | null>(null);
  const [colsOpen, setColsOpen] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [menu, setMenu] = useState<{ x: number; y: number; column: string; value: any } | null>(null);
  useEffect(() => setSearch(view.search || ''), [page.dataset.id, view.search]);
  useEffect(() => setSelected(new Set()), [page.dataset.id, page.offset, JSON.stringify(view)]);
  useEffect(() => {
    if (search === (view.search || '')) return;
    const id = setTimeout(() => onView({ ...view, search }), 350);
    return () => clearTimeout(id);
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = useMemo(() => page.columns.map((c, i) => ({ c, i, t: page.types[i] })).filter((x) => !view.hidden.includes(x.c)), [page, view.hidden]);

  const sortBy = (column: string, multi: boolean) => {
    const cur = view.sort.find((s) => s.column === column);
    const others = multi ? view.sort.filter((s) => s.column !== column) : [];
    const next = !cur ? [...others, { column, desc: false }] : !cur.desc ? [...others, { column, desc: true }] : others;
    onView({ ...view, sort: next });
  };
  const addFilter = (column: string, op: FilterOp, value?: any) => onView({ ...view, filters: [...view.filters, { column, op, value }] });
  const copy = () => {
    const rows = page.rows.filter((_, i) => selected.size === 0 || selected.has(i));
    const tsv = [visible.map((v) => v.c).join('\t'), ...rows.map((r) => visible.map((v) => fmt(r[v.i], v.t)).join('\t'))].join('\n');
    navigator.clipboard?.writeText(tsv);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" onClick={() => menu && setMenu(null)}>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-2 py-1.5 text-[11px]">
        <span className="flex items-center gap-1 rounded border border-line px-1.5 py-0.5">
          <Search size={11} className="text-faint" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search rows…" className="w-36 bg-transparent outline-none" />
        </span>
        {view.filters.map((f, i) => (
          <span key={i} className="flex items-center gap-1 rounded bg-accent/15 px-1.5 py-0.5 text-accent">
            <Filter size={10} /> {f.column} {OP_LABEL[f.op]} {NO_VALUE.includes(f.op) ? '' : String(f.value ?? '')}
            <button type="button" onClick={() => onView({ ...view, filters: view.filters.filter((_, k) => k !== i) })} aria-label="Remove filter"><X size={10} /></button>
          </span>
        ))}
        {adding ? (
          <span className="flex items-center gap-1 rounded border border-accent/50 px-1 py-0.5">
            <select value={adding.column} onChange={(e) => setAdding({ ...adding, column: e.target.value })} className="bg-transparent outline-none">
              {page.columns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={adding.op} onChange={(e) => setAdding({ ...adding, op: e.target.value as FilterOp })} className="bg-transparent outline-none">
              {(Object.keys(OP_LABEL) as FilterOp[]).map((o) => <option key={o} value={o}>{OP_LABEL[o]}</option>)}
            </select>
            {!NO_VALUE.includes(adding.op) && (
              <input autoFocus value={adding.value} onChange={(e) => setAdding({ ...adding, value: e.target.value })} placeholder="value" className="w-24 bg-transparent outline-none"
                onKeyDown={(e) => { if (e.key === 'Enter') { addFilter(adding.column, adding.op, adding.value); setAdding(null); } }} />
            )}
            <button type="button" onClick={() => { addFilter(adding.column, adding.op, adding.value); setAdding(null); }} className="rounded bg-accent px-1 text-bg">add</button>
            <button type="button" onClick={() => setAdding(null)}><X size={10} /></button>
          </span>
        ) : (
          <button type="button" onClick={() => setAdding({ column: page.columns[0] || '', op: 'contains', value: '' })}
            className="flex items-center gap-0.5 rounded border border-dashed border-line px-1.5 py-0.5 text-muted hover:text-fg"><Plus size={10} /> filter</button>
        )}
        {view.sort.map((s) => (
          <span key={s.column} className="flex items-center gap-0.5 rounded bg-elev px-1.5 py-0.5 text-muted">
            {s.desc ? <ArrowDown size={10} /> : <ArrowUp size={10} />} {s.column}
          </span>
        ))}
        <span className="relative">
          <button type="button" onClick={() => setColsOpen((o) => !o)} className="flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-muted hover:text-fg">
            <Columns3 size={11} /> columns {view.hidden.length ? `(${page.columns.length - view.hidden.length}/${page.columns.length})` : ''}
          </button>
          {colsOpen && (
            <div className="absolute left-0 top-full z-20 mt-1 max-h-72 w-56 overflow-y-auto rounded border border-line bg-elev p-1.5 shadow-xl">
              {page.columns.map((c, i) => (
                <label key={c} className="flex items-center gap-1.5 rounded px-1 py-0.5 hover:bg-panel">
                  <input type="checkbox" className="accent-[var(--accent)]" checked={!view.hidden.includes(c)}
                    onChange={(e) => onView({ ...view, hidden: e.target.checked ? view.hidden.filter((h) => h !== c) : [...view.hidden, c] })} />
                  <span className="truncate">{c}</span><span className="ml-auto text-[10px] text-faint">{page.types[i]}</span>
                </label>
              ))}
            </div>
          )}
        </span>
        {(view.filters.length > 0 || view.sort.length > 0 || view.hidden.length > 0 || view.search) && (
          <button type="button" onClick={() => onView({ filters: [], sort: [], hidden: [], search: '' })} className="text-muted underline hover:text-fg">reset view</button>
        )}
        <span className="ml-auto flex items-center gap-2 text-faint">
          <button type="button" onClick={copy} title="Copy rows (selected, or this page) as TSV" className="flex items-center gap-0.5 hover:text-fg">
            <Copy size={11} /> {selected.size ? `${selected.size} selected` : 'copy page'}
          </button>
          <span className="tabular-nums">{page.filtered.toLocaleString()}{page.filtered !== page.total ? ` of ${page.total.toLocaleString()}` : ''} rows{page.dataset.truncated ? ' (truncated)' : ''}</span>
        </span>
      </div>
      <div className={cx('min-h-0 flex-1 overflow-auto', busy && 'opacity-60')}>
        <table className="min-w-full border-separate border-spacing-0 text-[12px]">
          <thead className="sticky top-0 z-10 bg-panel">
            <tr>
              <th className="w-8 border-b border-r border-line px-1 text-right text-[10px] font-normal text-faint">
                <input type="checkbox" className="accent-[var(--accent)]" checked={selected.size > 0 && selected.size === page.rows.length}
                  onChange={(e) => setSelected(e.target.checked ? new Set(page.rows.map((_, i) => i)) : new Set())} aria-label="Select all" />
              </th>
              {visible.map(({ c, t }) => {
                const s = view.sort.find((x) => x.column === c);
                return (
                  <th key={c} className="border-b border-r border-line px-2 py-1 text-left font-semibold">
                    <button type="button" onClick={(e) => sortBy(c, e.shiftKey)} title="Sort (Shift: add a sort key)" className="flex w-full items-center gap-1 whitespace-nowrap">
                      <span>{c}</span>
                      <span className="text-[9px] font-normal uppercase text-faint">{t}</span>
                      {s && (s.desc ? <ArrowDown size={11} className="text-accent" /> : <ArrowUp size={11} className="text-accent" />)}
                      {s && view.sort.length > 1 && <span className="text-[9px] text-accent">{view.sort.indexOf(s) + 1}</span>}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {page.rows.map((r, i) => (
              <tr key={i} className={cx('hover:bg-elev/60', selected.has(i) && 'bg-accent/10')}>
                <td className="border-b border-r border-line px-1 text-right text-[10px] tabular-nums text-faint">
                  <label className="flex items-center justify-end gap-1">
                    {page.offset + i + 1}
                    <input type="checkbox" className="accent-[var(--accent)]" checked={selected.has(i)}
                      onChange={() => setSelected((s) => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n; })} />
                  </label>
                </td>
                {visible.map(({ c, i: j, t }) => (
                  <td key={c} onContextMenu={(e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, column: c, value: r[j] }); }}
                    className={cx('max-w-[380px] truncate border-b border-r border-line px-2 py-0.5', isNum(t) && 'text-right tabular-nums',
                      (r[j] === null || r[j] === undefined) && 'text-faint')} title={fmt(r[j], t)}>
                    {r[j] === null || r[j] === undefined ? '∅' : fmt(r[j], t)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {page.rows.length === 0 && <div className="p-4 text-[12px] text-faint">No row matches.</div>}
      </div>
      {menu && (
        <div className="fixed z-50 w-56 rounded border border-line bg-elev py-1 text-[12px] shadow-xl" style={{ left: menu.x, top: menu.y }}>
          {[
            ['Filter = this value', () => addFilter(menu.column, menu.value === null ? 'empty' : 'eq', menu.value)],
            ['Exclude this value', () => addFilter(menu.column, menu.value === null ? 'not_empty' : 'neq', menu.value)],
            ['Sort ascending', () => onView({ ...view, sort: [{ column: menu.column, desc: false }] })],
            ['Sort descending', () => onView({ ...view, sort: [{ column: menu.column, desc: true }] })],
            ['Hide this column', () => onView({ ...view, hidden: [...view.hidden, menu.column] })],
            ['Copy value', () => navigator.clipboard?.writeText(fmt(menu.value, ''))],
          ].map(([label, fn]) => (
            <button key={label as string} type="button" onClick={() => { (fn as () => void)(); setMenu(null); }} className="block w-full px-3 py-1 text-left hover:bg-accent/15">
              {label as string}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-center gap-2 border-t border-line px-2 py-1 text-[11px] text-faint">
        <button type="button" disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - page.limit))} className="rounded p-0.5 hover:text-fg disabled:opacity-30"><ChevronLeft size={13} /></button>
        <span className="tabular-nums">{page.filtered ? `${page.offset + 1}–${Math.min(page.offset + page.limit, page.filtered)}` : '0'} / {page.filtered.toLocaleString()}</span>
        <button type="button" disabled={page.offset + page.limit >= page.filtered} onClick={() => onPage(page.offset + page.limit)} className="rounded p-0.5 hover:text-fg disabled:opacity-30"><ChevronRight size={13} /></button>
        <span>Right-click a cell to filter by its value · Shift+click headers to sort by several columns</span>
      </div>
    </div>
  );
}
