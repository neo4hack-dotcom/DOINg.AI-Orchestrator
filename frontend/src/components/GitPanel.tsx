import { useCallback, useEffect, useState } from 'react';
import { ExternalLink, GitBranch, GitPullRequest, Loader2, RefreshCw } from 'lucide-react';
import type { GitStatus, PullRequest } from '../types';
import { api } from '../api';
import { useT } from '../i18n';

const inputCls = 'w-full rounded border border-line bg-bg px-2 py-1 text-[12px] outline-none focus:border-accent placeholder:text-faint';

/* Commit + push + Bitbucket pull request from the app. The description is drafted from the
   session (flow plan, tasks, checks, files) without spending tokens. */
export function GitPanel({ sessionId, busy, version, onOpenSettings }: { sessionId: string | null; busy: boolean; version: number; onOpenSettings: () => void }) {
  const t = useT();
  const [st, setSt] = useState<GitStatus | null>(null);
  const [form, setForm] = useState({ title: '', description: '', branch: '', target: '', commitMessage: '' });
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<PullRequest | null>(null);

  const load = useCallback((resetDraft = false) => {
    if (!sessionId) return;
    api.git(sessionId).then((s) => {
      setSt(s);
      if (s.draft) setForm((f) => (resetDraft || !f.title ? { ...f, title: s.draft!.title, description: s.draft!.description } : f));
    }).catch((e) => setError(e.message));
  }, [sessionId]);
  useEffect(() => load(true), [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => load(false), [version]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    if (!sessionId) return;
    setWorking(true);
    setError(null);
    setDone(null);
    try {
      const pr = await api.createPr(sessionId, form);
      setDone(pr);
      load(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setWorking(false);
    }
  };

  if (!st) return <div className="p-3"><Loader2 size={14} className="spin text-faint" /></div>;
  if (!st.isRepo) return <p className="p-3 text-[12px] text-faint">{t.gitNotRepo}</p>;
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="space-y-3 p-3 text-[12px]">
      <div className="space-y-1 rounded-md border border-line bg-panel px-2.5 py-2">
        <div className="flex items-center gap-1.5"><GitBranch size={12} className="text-accent" /> <b>{st.branch}</b>
          <span className="text-faint">· {st.changes?.length || 0} {t.uncommitted}</span>
          <button type="button" onClick={() => load(false)} className="ml-auto text-faint hover:text-fg" title={t.refresh}><RefreshCw size={11} /></button>
        </div>
        <div className="truncate text-faint" title={st.remoteUrl}>{t.remote}: {st.remoteUrl || t.noRemote}</div>
        {st.target?.project && <div className="text-faint">Bitbucket {st.target.kind}: <span className="text-muted">{st.target.project}/{st.target.repo}</span></div>}
        {st.target?.error && <div className="text-warn">{st.target.error}</div>}
      </div>

      {!st.configured && (
        <button type="button" onClick={onOpenSettings} className="w-full rounded border border-warn/50 bg-warn/5 px-2.5 py-1.5 text-left text-warn">{t.bitbucketMissing}</button>
      )}

      <label className="block space-y-1"><span className="text-muted">{t.prTitle}</span><input className={inputCls} value={form.title} onChange={set('title')} /></label>
      <label className="block space-y-1"><span className="text-muted">{t.prDescription}</span>
        <textarea className={`${inputCls} min-h-[150px] font-mono`} value={form.description} onChange={set('description')} spellCheck={false} /></label>
      <label className="block space-y-1"><span className="text-muted">{t.prBranch}</span><input className={inputCls} value={form.branch} onChange={set('branch')} placeholder="supercode/…" /></label>
      <label className="block space-y-1"><span className="text-muted">{t.prTarget}</span><input className={inputCls} value={form.target} onChange={set('target')} placeholder="develop" /></label>
      <label className="block space-y-1"><span className="text-muted">{t.prCommit}</span><input className={inputCls} value={form.commitMessage} onChange={set('commitMessage')} /></label>

      <button type="button" disabled={busy || working || !form.title.trim() || !st.remoteUrl} onClick={submit}
        className="inline-flex w-full items-center justify-center gap-1.5 rounded bg-accent px-3 py-1.5 font-semibold text-bg hover:brightness-110 disabled:opacity-40">
        {working ? <Loader2 size={13} className="spin" /> : <GitPullRequest size={13} />} {working ? t.creatingPr : t.createPr}
      </button>
      {error && <p className="whitespace-pre-wrap break-words text-err">{error}</p>}
      {done && (
        <a href={done.url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 rounded border border-ok/50 bg-ok/5 px-2.5 py-1.5 text-ok">
          <ExternalLink size={12} /> {t.prCreated}: {done.branch} → {done.target}
        </a>
      )}

      {st.pullRequests && st.pullRequests.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wider text-faint">{t.prList}</div>
          <ul className="space-y-1">
            {st.pullRequests.slice().reverse().map((pr) => (
              <li key={`${pr.url}-${pr.createdAt}`}>
                <a href={pr.url} target="_blank" rel="noreferrer" className="block min-w-0 text-muted hover:text-fg">
                  <span className="flex min-w-0 items-center gap-1.5"><GitPullRequest size={11} className="shrink-0 text-accent" /> <span className="truncate">{pr.title}</span></span>
                  <span className="block truncate pl-[18px] text-[11px] text-faint">{pr.branch} → {pr.target}</span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
