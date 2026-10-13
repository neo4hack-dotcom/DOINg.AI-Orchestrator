import { useCallback, useEffect, useState } from 'react';
import { ChevronRight, Code2, Loader2, Plus, Puzzle, RotateCw, Trash2, Wand2, X } from 'lucide-react';
import type { ModInfo } from '../types';
import { api } from '../api';
import { cx } from '../util';

const inputCls = 'w-full rounded border border-line bg-bg px-2.5 py-1.5 text-fg outline-none focus:border-accent placeholder:text-faint';

function OptionInput({ value, onChange }: { value: any; onChange: (v: any) => void }) {
  if (typeof value === 'boolean') return <input type="checkbox" className="accent-[var(--accent)]" checked={value} onChange={(e) => onChange(e.target.checked)} />;
  if (typeof value === 'number') return <input className={`${inputCls} w-28`} type="number" value={value} onChange={(e) => onChange(Number(e.target.value))} />;
  if (Array.isArray(value)) {
    return <textarea className={`${inputCls} min-h-[60px] font-mono text-[12px]`} value={value.join('\n')} onChange={(e) => onChange(e.target.value.split('\n').map((x) => x.trim()).filter(Boolean))} />;
  }
  if (value && typeof value === 'object') {
    return <textarea className={`${inputCls} min-h-[60px] font-mono text-[12px]`} defaultValue={JSON.stringify(value, null, 1)}
      onBlur={(e) => { try { onChange(JSON.parse(e.target.value)); } catch { /* keep the old value */ } }} />;
  }
  return <input className={inputCls} value={value ?? ''} onChange={(e) => onChange(e.target.value)} />;
}

function ModCard({ mod, onChanged, onMsg }: { mod: ModInfo; onChanged: (mods: ModInfo[]) => void; onMsg: (ok: boolean, text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<Record<string, any>>(mod.options);
  const [source, setSource] = useState<Record<string, string> | null>(null);
  const [file, setFile] = useState('mod.py');
  const [saving, setSaving] = useState(false);
  useEffect(() => setOptions(mod.options), [mod.options]);

  const loadSource = async () => {
    const r = await api.modSource(mod.name);
    setSource(r.files);
    setFile(r.files['mod.py'] !== undefined ? 'mod.py' : Object.keys(r.files)[0]);
  };

  const saveSource = async () => {
    if (!source) return;
    setSaving(true);
    try {
      const r = await api.saveModSource(mod.name, source);
      onChanged(r.mods);
      onMsg(r.validation.ok, r.validation.ok ? `${mod.name} reloaded: ${r.validation.events.length} hook(s), ${r.validation.commands.length} command(s).` : `${mod.name} does not load: ${r.validation.error}`);
    } catch (e: any) {
      onMsg(false, e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={cx('rounded-md border bg-panel', mod.error ? 'border-err/40' : 'border-line')}>
      <div className="flex min-w-0 items-center gap-2 px-3 py-2">
        <button type="button" onClick={() => setOpen((o) => !o)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <ChevronRight size={12} className={cx('shrink-0 text-faint transition-transform', open && 'rotate-90')} />
          <Puzzle size={13} className={mod.enabled ? 'shrink-0 text-accent' : 'shrink-0 text-faint'} />
          <span className="shrink-0 whitespace-nowrap font-semibold">{mod.name}</span>
          <span className="shrink-0 text-[10px] text-faint">v{mod.version}</span>
          <span className={cx('shrink-0 whitespace-nowrap rounded px-1 text-[10px]', mod.scope === 'builtin' ? 'bg-elev text-faint' : 'bg-accent/15 text-accent')}>{mod.scope === 'builtin' ? 'built-in' : 'yours'}</span>
          {mod.errors > 0 && <span className="rounded bg-err/15 px-1 text-[10px] text-err">{mod.errors} error(s)</span>}
          <span className="min-w-0 truncate text-[12px] text-muted">{mod.description}</span>
        </button>
        <label className="flex shrink-0 items-center gap-1 text-[11px] text-muted">
          <input type="checkbox" className="accent-[var(--accent)]" checked={mod.enabled} disabled={Boolean(mod.error)}
            onChange={(e) => api.toggleMod(mod.name, e.target.checked).then(onChanged).catch((er) => onMsg(false, er.message))} /> on
        </label>
      </div>
      {open && (
        <div className="space-y-3 border-t border-line px-3 py-2.5 text-[12px]">
          {mod.error && <pre className="whitespace-pre-wrap rounded bg-err/10 px-2 py-1 text-[11px] text-err">{mod.error}</pre>}
          {mod.inspiredBy && <div className="text-[11px] text-faint">Inspired by: {mod.inspiredBy}</div>}
          <div className="flex flex-wrap gap-1">
            {mod.events.map((e) => <span key={e} className="rounded border border-line px-1.5 font-mono text-[10px] text-muted">{e}</span>)}
            {mod.commands.map((c) => <span key={c.name} className="rounded border border-accent/40 px-1.5 font-mono text-[10px] text-accent">/{c.name}</span>)}
            {mod.tools.map((t) => <span key={t.name} className="rounded border border-ok/40 px-1.5 font-mono text-[10px] text-ok">tool {t.name}</span>)}
          </div>
          {Object.keys(options).length > 0 && (
            <div className="space-y-2">
              <div className="font-semibold text-muted">Options</div>
              {Object.entries(options).map(([k, v]) => (
                <label key={k} className="block space-y-0.5">
                  <span className="font-mono text-[11px]">{k}</span>
                  <OptionInput value={v} onChange={(nv) => setOptions((o) => ({ ...o, [k]: nv }))} />
                  {mod.optionDocs[k] && <span className="block text-[11px] text-faint">{mod.optionDocs[k]}</span>}
                </label>
              ))}
              <button type="button" onClick={() => api.modOptions(mod.name, options).then((m) => { onChanged(m); onMsg(true, `Options of ${mod.name} saved.`); })}
                className="rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg">Save options</button>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => (source ? setSource(null) : loadSource())} className="inline-flex items-center gap-1 rounded border border-line px-2 py-1 hover:border-accent/60">
              <Code2 size={12} /> {source ? 'Hide source' : mod.readOnly ? 'View source' : 'Edit source'}
            </button>
            {mod.scope === 'user' && (
              <button type="button" onClick={() => window.confirm(`Delete the mod ${mod.name}?`) && api.deleteMod(mod.name).then(onChanged)}
                className="inline-flex items-center gap-1 rounded border border-line px-2 py-1 text-muted hover:text-err"><Trash2 size={12} /> Delete</button>
            )}
          </div>
          {source && (
            <div className="space-y-1.5">
              <div className="flex flex-wrap gap-1">
                {Object.keys(source).map((f) => (
                  <button key={f} type="button" onClick={() => setFile(f)} className={cx('rounded border px-2 py-0.5 font-mono text-[11px]', f === file ? 'border-accent text-accent' : 'border-line text-muted')}>{f}</button>
                ))}
              </div>
              <textarea className={`${inputCls} min-h-[260px] font-mono text-[11.5px] leading-[1.5]`} spellCheck={false} value={source[file] ?? ''}
                onChange={(e) => setSource({ ...source, [file]: e.target.value })} />
              <button type="button" onClick={saveSource} disabled={saving} className="rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg disabled:opacity-40">
                {mod.readOnly ? 'Save as my copy (replaces the built-in)' : 'Save and reload'}
              </button>
            </div>
          )}
          {mod.logs.length > 0 && (
            <details>
              <summary className="cursor-pointer text-muted">Logs ({mod.logs.length})</summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg px-2 py-1 text-[11px] text-muted">
                {mod.logs.map((l) => `${new Date(l.t).toLocaleTimeString()} [${l.level}] ${l.text}`).join('\n')}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

/* Settings > Mods: Python hooks on the agent's events (Claude Code-style mods). */
export function ModsManager({ onRunCommand }: { onRunCommand?: (text: string) => void }) {
  const [mods, setMods] = useState<ModInfo[]>([]);
  const [request, setRequest] = useState('');
  const [blank, setBlank] = useState<{ name: string; description: string } | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => api.mods().then(setMods).catch((e) => setMsg({ ok: false, text: e.message })), []);
  useEffect(() => { load(); }, [load]);
  const say = (ok: boolean, text: string) => setMsg({ ok, text });

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-accent/30 bg-accent/5 p-3 text-[12px] text-muted">
        <div className="mb-1 flex items-center gap-1.5 font-semibold text-accent"><Puzzle size={13} /> Mods</div>
        Mods hook into the agent like Claude Code mods: block or rewrite tool calls, post-process results, add system-prompt sections, react to
        turns and flow phases, add slash commands, a status line item, a band above the prompt or a side-panel section. Five built-in mods are on
        by default; yours live in <code>data/mods</code>. A failing mod is isolated and logged, never fatal.
      </div>

      <div className="space-y-2 rounded-md border border-line p-3">
        <div className="flex items-center gap-1.5 text-[12px] font-semibold"><Wand2 size={13} className="text-accent" /> Create a mod by describing it</div>
        <textarea className={`${inputCls} min-h-[56px]`} value={request} onChange={(e) => setRequest(e.target.value)}
          placeholder="e.g. Block any edit under migrations/ unless I said 'migration' in my request, and show the number of blocked edits in the status line" />
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={!request.trim() || !onRunCommand} onClick={() => { onRunCommand?.(`/mod ${request.trim()}`); setRequest(''); say(true, 'The mod builder is working in the current session: approve the installation when asked.'); }}
            className="inline-flex items-center gap-1 rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg disabled:opacity-40"><Wand2 size={12} /> Build it (/mod)</button>
          <button type="button" onClick={() => setBlank(blank ? null : { name: '', description: '' })}
            className="inline-flex items-center gap-1 rounded border border-line px-3 py-1 text-[12px] hover:border-accent/60"><Plus size={12} /> Blank mod</button>
          <button type="button" onClick={() => { setLoading(true); api.reloadMods().then(setMods).finally(() => setLoading(false)); }}
            className="ml-auto inline-flex items-center gap-1 rounded border border-line px-2 py-1 text-[12px] text-muted hover:text-fg">
            {loading ? <Loader2 size={12} className="spin" /> : <RotateCw size={12} />} Reload all
          </button>
        </div>
        {!onRunCommand && <p className="text-[11px] text-faint">Open a session to build mods with the agent.</p>}
        {blank && (
          <div className="flex flex-wrap gap-2">
            <input className={`${inputCls} w-48`} placeholder="name (lowercase-hyphens)" value={blank.name} onChange={(e) => setBlank({ ...blank, name: e.target.value })} />
            <input className={`${inputCls} min-w-0 flex-1`} placeholder="description" value={blank.description} onChange={(e) => setBlank({ ...blank, description: e.target.value })} />
            <button type="button" disabled={!blank.name.trim()} onClick={() => api.createMod(blank.name, blank.description)
              .then((r) => { setMods(r.mods); setBlank(null); say(true, `${blank.name} created from the template: edit its source below.`); })
              .catch((e) => say(false, e.message))} className="rounded bg-accent px-3 text-[12px] font-semibold text-bg disabled:opacity-40">Create</button>
            <button type="button" onClick={() => setBlank(null)} className="text-faint hover:text-fg"><X size={14} /></button>
          </div>
        )}
      </div>

      {msg && <div className={cx('rounded px-2 py-1 text-[12px]', msg.ok ? 'bg-ok/10 text-ok' : 'bg-err/10 text-err')}>{msg.text}</div>}

      <div className="space-y-2">
        {mods.map((m) => <ModCard key={`${m.scope}-${m.name}`} mod={m} onChanged={setMods} onMsg={say} />)}
      </div>
    </div>
  );
}
