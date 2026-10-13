import { useEffect, useState } from 'react';
import { CheckCircle2, Globe, HardDrive, Loader2, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { AppConfig, PrivacyReport } from '../types';
import { api } from '../api';
import { cx } from '../util';

/* Settings > Privacy & security: what leaves this machine, and the protections in force. */
export function PrivacySettings({ cfg, setCfg, saved }: { cfg: AppConfig; setCfg: React.Dispatch<React.SetStateAction<AppConfig>>; saved: AppConfig }) {
  const [report, setReport] = useState<PrivacyReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.privacy().then(setReport).catch((e) => setError(e.message));
  }, []);
  const setNet = (patch: Partial<AppConfig['network']>) => setCfg((c) => ({ ...c, network: { ...c.network, ...patch } }));
  const dirty = cfg.network.localOnly !== saved.network.localOnly || cfg.network.allowPackageManagers !== saved.network.allowPackageManagers;
  const failing = report?.checks.filter((c) => !c.ok).length || 0;

  return (
    <div className="space-y-4 text-[12.5px]">
      <div className={cx('flex items-start gap-2 rounded-md border px-3 py-2', failing ? 'border-warn/40 bg-warn/5' : 'border-ok/40 bg-ok/5')}>
        {failing ? <ShieldAlert size={16} className="mt-0.5 shrink-0 text-warn" /> : <ShieldCheck size={16} className="mt-0.5 shrink-0 text-ok" />}
        <div>
          <div className={cx('font-semibold', failing ? 'text-warn' : 'text-ok')}>
            {!report ? 'Checking…' : failing ? `${failing} point${failing > 1 ? 's' : ''} to review` : 'Everything stays on this machine'}
          </div>
          <div className="text-[11px] text-muted">
            The app has no telemetry and no cloud service. Your code, prompts and data only go to the endpoints listed below, which you configured.
          </div>
        </div>
      </div>

      <section className="space-y-2">
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={cfg.network.localOnly} onChange={(e) => setNet({ localOnly: e.target.checked })} className="mt-1 accent-[var(--accent)]" />
          <span>
            Local-only mode
            <span className="block text-[11px] text-faint">
              Every request the app makes (LLM, MCP servers over HTTP, webfetch, previews) must go to this machine or a private network; anything else is
              refused before it leaves. Shell commands that reach the network (curl, git push, ssh…) always ask first, and are never run unattended in Auto mode.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={cfg.network.allowPackageManagers} onChange={(e) => setNet({ allowPackageManagers: e.target.checked })}
            className="mt-1 accent-[var(--accent)]" disabled={!cfg.network.localOnly} />
          <span className={cx(!cfg.network.localOnly && 'opacity-50')}>
            Allow package installs (npm install, pip install…)
            <span className="block text-[11px] text-faint">They download from the registries you configured (point them to an internal mirror for a fully offline setup).</span>
          </span>
        </label>
        {dirty && <p className="text-[11px] text-warn">Save to apply. The report below shows the saved settings.</p>}
      </section>

      {error && <p className="text-err">{error}</p>}
      {!report && !error && <p className="flex items-center gap-2 text-muted"><Loader2 size={13} className="spin" /> Loading…</p>}
      {report && (
        <>
          <section>
            <h3 className="mb-1.5 font-semibold">Security checks</h3>
            <ul className="divide-y divide-line rounded-md border border-line">
              {report.checks.map((c) => (
                <li key={c.name} className="flex items-start gap-2 px-3 py-1.5">
                  {c.ok ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-ok" /> : <ShieldAlert size={14} className="mt-0.5 shrink-0 text-warn" />}
                  <div className="min-w-0">
                    <div>{c.name}</div>
                    <div className="text-[11px] text-faint">{c.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h3 className="mb-1.5 font-semibold">Where data can go</h3>
            <ul className="divide-y divide-line rounded-md border border-line">
              {report.endpoints.map((e, i) => (
                <li key={`${e.kind}-${i}`} className="flex items-start gap-2 px-3 py-1.5">
                  {e.local ? <HardDrive size={14} className="mt-0.5 shrink-0 text-ok" /> : <Globe size={14} className={cx('mt-0.5 shrink-0', e.blocked ? 'text-faint' : 'text-warn')} />}
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                      <span>{e.name}</span>
                      <span className={cx('rounded px-1 text-[10px]', e.local ? 'bg-ok/15 text-ok' : e.blocked ? 'bg-elev text-faint' : 'bg-warn/15 text-warn')}>
                        {e.local ? 'local' : e.blocked ? 'blocked' : 'internet'}
                      </span>
                    </div>
                    {e.url && <div className="truncate font-mono text-[11px] text-muted">{e.url}</div>}
                    {e.note && <div className="text-[11px] text-faint">{e.note}</div>}
                  </div>
                </li>
              ))}
            </ul>
          </section>
          <section className="space-y-1 text-[11px] text-faint">
            <p>
              Server: <span className="font-mono">{report.host}</span>{report.token ? ' · access token required' : ''}. Start with
              <span className="font-mono"> python server.py --host 0.0.0.0 --token &lt;secret&gt;</span> only if you need it from another machine.
            </p>
            <p>
              Agents work inside the project folder: they cannot read or write outside it, nor the app's own data folder or <span className="font-mono">.git</span>.
              Programs they run (tests, builds) are not sandboxed by the OS: keep the default <b>Ask</b> mode for projects you do not trust.
            </p>
          </section>
        </>
      )}
    </div>
  );
}
