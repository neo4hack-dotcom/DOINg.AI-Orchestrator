import { useCallback, useEffect, useState } from 'react';
import { Copy, FilePlus2, FolderInput, Loader2, Pencil, Plus, RotateCw, Sparkles, Trash2, Wand2, X } from 'lucide-react';
import type { SkillSummary } from '../types';
import { api } from '../api';
import { cx } from '../util';

const inputCls = 'w-full rounded border border-line bg-bg px-2.5 py-1.5 text-fg outline-none focus:border-accent placeholder:text-faint';

const TEMPLATE = (name: string) => `---
name: ${name || 'my-skill'}
description: What this skill does. Use when the user asks to ..., mentions ..., or when ...
argument-hint: "<target>"
---

# ${name || 'My skill'}

When to use: ...

## Workflow
1. ...
2. ...

## Definition of done
- ...

## Pitfalls
- ...
`;

type Editor = {
  scope: 'user' | 'project';
  name: string;
  renameFrom?: string;
  content: string;
  files: Record<string, string | null>;   // edited / added / removed (null) reference files
  existingFiles: string[];
  current: string;                        // SKILL.md or a reference file path
};

const SCOPE_LABEL: Record<string, string> = { project: 'Project', user: 'Personal', builtin: 'Built-in' };

/* Settings > Skills: Agent Skills (SKILL.md folders) - list, enable, create, edit, AI-generate, import. */
export function SkillsManager({ directory, onRunCommand }: { directory: string; onRunCommand?: (text: string) => void }) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [aiRequest, setAiRequest] = useState('');
  const [busy, setBusy] = useState(false);
  const [importPath, setImportPath] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => api.skills(directory).then(setSkills).catch((e) => setMsg({ ok: false, text: e.message })), [directory]);
  useEffect(() => { load(); }, [load]);

  const open = async (s: SkillSummary) => {
    const d = await api.skill(s.name, directory);
    setEditor({ scope: s.scope === 'project' ? 'project' : 'user', name: d.name, renameFrom: d.readOnly ? undefined : d.name, content: d.content,
      files: {}, existingFiles: d.files, current: 'SKILL.md' });
  };

  const openFile = async (path: string) => {
    if (!editor) return;
    if (editor.files[path] === undefined && editor.existingFiles.includes(path) && editor.renameFrom) {
      const text = await api.skillFile(editor.renameFrom, path, directory).catch(() => '');
      setEditor({ ...editor, files: { ...editor.files, [path]: text }, current: path });
    } else setEditor({ ...editor, current: path });
  };

  const save = async () => {
    if (!editor) return;
    setBusy(true);
    try {
      const nameMatch = /^name:\s*(.+)$/m.exec(editor.content);
      const name = (nameMatch?.[1] || editor.name).trim().replace(/^["']|["']$/g, '');
      await api.saveSkill({ scope: editor.scope, name, content: editor.content, files: editor.files, renameFrom: editor.renameFrom, directory });
      setMsg({ ok: true, text: `Saved ${name} (${SCOPE_LABEL[editor.scope]}). Run it with /${name} or let the agent load it.` });
      setEditor(null);
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const generate = async (improve: boolean) => {
    if (!aiRequest.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.generateSkill(aiRequest, improve && editor ? editor.content : '');
      setEditor((e) => ({
        ...(e || { scope: 'user', files: {}, existingFiles: [], current: 'SKILL.md', name: r.name }),
        content: r.content, name: e?.name || r.name, current: 'SKILL.md',
      }));
      setMsg(r.errors.length ? { ok: false, text: `Review before saving: ${r.errors.join('; ')}` } : { ok: true, text: 'Draft ready: review it, then save.' });
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const groups = (['project', 'user', 'builtin'] as const).map((scope) => [scope, skills.filter((s) => s.scope === scope)] as const);
  const currentText = editor ? (editor.current === 'SKILL.md' ? editor.content : editor.files[editor.current] ?? '') : '';
  const fileList = editor ? [...new Set([...editor.existingFiles, ...Object.keys(editor.files)])].filter((f) => editor.files[f] !== null) : [];

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-accent/30 bg-accent/5 p-3 text-[12px] text-muted">
        <div className="mb-1 flex items-center gap-1.5 font-semibold text-accent"><Sparkles size={13} /> Agent Skills</div>
        A skill is a folder with a <code>SKILL.md</code> (name + description + instructions) and optional reference files. The agents only see
        each skill's description and load the full instructions when a task matches (progressive disclosure, like Claude Code). Run one directly
        with <code>/skill-name arguments</code>. Personal skills live in <code>data/skills</code>; project skills in <code>.supercode/skills</code>
        (also read: <code>.claude/skills</code>, <code>.agents/skills</code>) and travel with the repository.
      </div>

      <div className="space-y-2 rounded-md border border-line p-3">
        <div className="flex items-center gap-1.5 text-[12px] font-semibold"><Wand2 size={13} className="text-accent" /> Create with AI</div>
        <textarea className={`${inputCls} min-h-[56px]`} value={aiRequest} onChange={(e) => setAiRequest(e.target.value)}
          placeholder="e.g. Add a REST endpoint in our FastAPI app: router, pydantic models, tests with httpx, update the OpenAPI docs" />
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => generate(false)} disabled={busy || !aiRequest.trim()} className="inline-flex items-center gap-1 rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg disabled:opacity-40">
            {busy ? <Loader2 size={12} className="spin" /> : <Wand2 size={12} />} Draft a skill
          </button>
          {editor && <button type="button" onClick={() => generate(true)} disabled={busy || !aiRequest.trim()} className="rounded border border-line px-3 py-1 text-[12px] hover:border-accent/60 disabled:opacity-40">Improve the open skill</button>}
          {onRunCommand && (
            <button type="button" onClick={() => aiRequest.trim() && onRunCommand(`/skill-new ${aiRequest.trim()}`)} disabled={!aiRequest.trim()}
              className="rounded border border-line px-3 py-1 text-[12px] hover:border-accent/60 disabled:opacity-40" title="The build agent researches and writes the skill (and its reference files) in the current session">
              Let the agent build it (/skill-new)
            </button>
          )}
          <button type="button" onClick={() => setEditor({ scope: 'user', name: '', content: TEMPLATE(''), files: {}, existingFiles: [], current: 'SKILL.md' })}
            className="ml-auto inline-flex items-center gap-1 rounded border border-line px-3 py-1 text-[12px] hover:border-accent/60"><Plus size={12} /> Blank skill</button>
        </div>
      </div>

      {msg && <div className={cx('rounded px-2 py-1 text-[12px]', msg.ok ? 'bg-ok/10 text-ok' : 'bg-err/10 text-err')}>{msg.text}</div>}

      {editor && (
        <div className="space-y-2 rounded-md border border-accent/40 bg-panel p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{editor.renameFrom ? `Edit ${editor.renameFrom}` : 'New skill'}</span>
            <select className="rounded border border-line bg-bg px-2 py-1 text-[12px]" value={editor.scope} onChange={(e) => setEditor({ ...editor, scope: e.target.value as Editor['scope'], renameFrom: undefined })}>
              <option value="user">Personal (all projects)</option>
              <option value="project">This project (.supercode/skills)</option>
            </select>
            <button type="button" onClick={() => setEditor(null)} className="ml-auto text-faint hover:text-fg"><X size={14} /></button>
          </div>
          <div className="flex flex-wrap gap-1">
            {['SKILL.md', ...fileList].map((f) => (
              <button key={f} type="button" onClick={() => (f === 'SKILL.md' ? setEditor({ ...editor, current: f }) : openFile(f))}
                className={cx('group flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px]', editor.current === f ? 'border-accent text-accent' : 'border-line text-muted')}>
                {f}
                {f !== 'SKILL.md' && (
                  <span role="button" tabIndex={0} title="Remove file" onClick={(e) => { e.stopPropagation(); setEditor({ ...editor, files: { ...editor.files, [f]: null }, current: 'SKILL.md' }); }}
                    className="text-faint hover:text-err"><X size={10} /></span>
                )}
              </button>
            ))}
            <button type="button" onClick={() => {
              const path = window.prompt('New file path inside the skill (e.g. references/api.md, scripts/check.py)');
              if (path) setEditor({ ...editor, files: { ...editor.files, [path]: '' }, current: path });
            }} className="inline-flex items-center gap-1 rounded border border-dashed border-line px-2 py-0.5 text-[11px] text-faint hover:text-fg"><FilePlus2 size={11} /> file</button>
          </div>
          <textarea className={`${inputCls} min-h-[300px] font-mono text-[12px] leading-[1.5]`} value={currentText} spellCheck={false}
            onChange={(e) => setEditor(editor.current === 'SKILL.md' ? { ...editor, content: e.target.value } : { ...editor, files: { ...editor.files, [editor.current]: e.target.value } })} />
          <div className="flex items-center gap-2">
            <button type="button" onClick={save} disabled={busy} className="rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg disabled:opacity-40">Save skill</button>
            <span className="text-[11px] text-faint">Frontmatter: name, description (what + when), optional argument-hint, allowed-tools, disable-model-invocation, user-invocable.</span>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <span className="font-semibold">Installed skills</span>
        <button type="button" onClick={load} className="ml-auto rounded p-1 text-faint hover:text-fg" title="Refresh"><RotateCw size={13} /></button>
      </div>
      {groups.map(([scope, list]) => list.length > 0 && (
        <div key={scope} className="space-y-1.5">
          <div className="text-[11px] uppercase tracking-wider text-faint">{SCOPE_LABEL[scope]}</div>
          {list.map((s) => (
            <div key={`${s.scope}-${s.name}`} className={cx('rounded-md border border-line bg-panel px-3 py-2', (!s.enabled || !s.available) && 'opacity-60')}>
              <div className="flex min-w-0 items-center gap-2">
                <span className="font-semibold">{s.name}</span>
                {s.userInvocable && <span className="rounded bg-elev px-1 font-mono text-[10px] text-muted">/{s.name}{s.argumentHint ? ` ${s.argumentHint}` : ''}</span>}
                {!s.modelInvocable && <span className="rounded bg-elev px-1 text-[10px] text-faint">manual only</span>}
                {s.requires === 'reverse' && <span className={cx('rounded px-1 text-[10px]', s.available ? 'bg-warn/15 text-warn' : 'bg-elev text-faint')}>{s.available ? 'reverse layer' : 'needs the reverse layer'}</span>}
                {s.error && <span className="rounded bg-err/15 px-1 text-[10px] text-err" title={s.error}>invalid</span>}
                <div className="ml-auto flex shrink-0 items-center gap-1">
                  <label className="mr-1 flex items-center gap-1 text-[11px] text-muted">
                    <input type="checkbox" className="accent-[var(--accent)]" checked={s.enabled} onChange={(e) => api.toggleSkill(s.name, e.target.checked).then(load)} /> on
                  </label>
                  {s.readOnly ? (
                    <>
                      <button type="button" title="View" onClick={() => open(s)} className="rounded p-1 text-faint hover:text-fg"><Pencil size={12} /></button>
                      <button type="button" title="Duplicate into my skills to customize it" onClick={() => {
                        const name = window.prompt('Name of the copy', `${s.name}-custom`);
                        if (name) api.duplicateSkill(s.name, 'user', name, directory).then(() => { load(); setMsg({ ok: true, text: `Copied to ${name}.` }); }).catch((e) => setMsg({ ok: false, text: e.message }));
                      }} className="rounded p-1 text-faint hover:text-fg"><Copy size={12} /></button>
                    </>
                  ) : (
                    <>
                      <button type="button" title="Edit" onClick={() => open(s)} className="rounded p-1 text-faint hover:text-fg"><Pencil size={12} /></button>
                      <button type="button" title="Delete" onClick={() => window.confirm(`Delete the skill ${s.name}?`) && api.deleteSkill(s.scope, s.name, directory).then(load)}
                        className="rounded p-1 text-faint hover:text-err"><Trash2 size={12} /></button>
                    </>
                  )}
                </div>
              </div>
              <div className="mt-0.5 text-[12px] text-muted">{s.description}</div>
              {s.license && <div className="text-[10px] text-faint">{s.license}</div>}
            </div>
          ))}
        </div>
      ))}

      <div className="space-y-1.5 rounded-md border border-line p-3">
        <div className="flex items-center gap-1.5 text-[12px] font-semibold"><FolderInput size={13} /> Import skills from a folder</div>
        <div className="flex gap-2">
          <input className={inputCls} value={importPath} onChange={(e) => setImportPath(e.target.value)} placeholder="C:\\path\\to\\skills or ~/repo/.claude/skills/my-skill" />
          <button type="button" disabled={!importPath.trim()} onClick={() => api.importSkills(importPath, 'user', directory)
            .then((r) => { setMsg({ ok: true, text: `Imported: ${r.imported.map((s) => s.name).join(', ')}` }); load(); })
            .catch((e) => setMsg({ ok: false, text: e.message }))} className="shrink-0 rounded border border-line px-3 text-[12px] hover:border-accent/60 disabled:opacity-40">Import</button>
        </div>
        <p className="text-[11px] text-faint">Works with Claude Code / Agent Skills folders (one skill, or a folder of skills).</p>
      </div>
    </div>
  );
}
