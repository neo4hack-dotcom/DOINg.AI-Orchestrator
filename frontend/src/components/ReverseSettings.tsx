import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, Microscope, Stethoscope, XCircle } from 'lucide-react';
import type { AppConfig, ReverseStatus } from '../types';
import { api } from '../api';
import { cx } from '../util';
import { Markdown } from './Markdown';

const inputCls = 'w-full rounded border border-line bg-bg px-2.5 py-1.5 text-fg outline-none focus:border-accent placeholder:text-faint';

/* Settings > Reverse engineering: the optional REA layer (https://github.com/morluto/rea, MIT) - MCP server + skill, lawful use only. */
export function ReverseSettings({ cfg, setCfg }: { cfg: AppConfig; setCfg: (fn: (c: AppConfig) => AppConfig) => void }) {
  const [status, setStatus] = useState<ReverseStatus | null>(null);
  const [doctor, setDoctor] = useState<{ running: boolean; output?: string; ok?: boolean; command?: string }>({ running: false });
  const [envText, setEnvText] = useState(Object.entries(cfg.reverse.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'));
  const rev = cfg.reverse;
  const set = (patch: Partial<AppConfig['reverse']>) => setCfg((c) => ({ ...c, reverse: { ...c.reverse, ...patch } }));

  useEffect(() => { api.reverse().then(setStatus).catch(() => {}); }, []);
  useEffect(() => {
    const env: Record<string, string> = {};
    envText.split('\n').forEach((l) => {
      const i = l.indexOf('=');
      if (i > 0) env[l.slice(0, i).trim()] = l.slice(i + 1).trim();
    });
    set({ env });
  }, [envText]); // eslint-disable-line react-hooks/exhaustive-deps

  const runDoctor = async () => {
    setDoctor({ running: true });
    try {
      const r = await api.reverseDoctor();
      setDoctor({ running: false, output: r.output, ok: r.exitCode === 0, command: r.command });
    } catch (e: any) {
      setDoctor({ running: false, output: e.message, ok: false });
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-warn/40 bg-warn/5 p-3 text-[12px] text-muted">
        <div className="mb-1 flex items-center gap-1.5 font-semibold text-warn"><Microscope size={13} /> Reverse engineering layer (optional) - REA</div>
        Adds <a className="text-accent underline" href="https://github.com/morluto/rea" target="_blank" rel="noreferrer">REA - Reverse Engineer Anything</a> to
        SuperCode with its exact feature set: its MCP server (<code>npx -y rea-agents@{rev.version || '6.4.0'} mcp</code>, named <code>rea</code>) for native binaries
        (Hopper / Ghidra / IDA), Go / Rust / .NET / WASM / EVM artifacts, JavaScript and Electron apps, websites, network captures, Android, firmware,
        SQLite and process behavior - and its <code>reverse-engineer-anything</code> skill (vendored, MIT) that teaches the agent REA's evidence-first workflows.
        Use <code>/reverse &lt;target&gt; &lt;question&gt;</code> in a session.
      </div>

      <details className="rounded-md border border-line bg-panel px-3 py-2 text-[12px]" open={!rev.acknowledged}>
        <summary className="cursor-pointer font-semibold"><AlertTriangle size={12} className="mr-1 inline text-warn" />Responsible use (REA disclaimer)</summary>
        <div className="mt-2 max-h-56 overflow-y-auto pr-2 text-muted"><Markdown text={status?.disclaimer || 'Loading…'} /></div>
      </details>

      <label className="flex items-start gap-2 text-[12px]">
        <input type="checkbox" className="mt-0.5 accent-[var(--accent)]" checked={rev.acknowledged}
          onChange={(e) => set({ acknowledged: e.target.checked, enabled: e.target.checked ? rev.enabled : false })} />
        <span>I will only analyze software I own or am authorized to examine, for lawful purposes (security research, interoperability, debugging,
          education), and I am responsible for complying with applicable laws and licenses.</span>
      </label>
      <label className={cx('flex items-center gap-2 text-[12px]', !rev.acknowledged && 'opacity-50')}>
        <input type="checkbox" className="accent-[var(--accent)]" disabled={!rev.acknowledged} checked={rev.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        <span className="font-semibold">Enable the reverse-engineering layer</span>
        <span className="text-faint">(MCP server <code>rea</code> + skill + <code>/reverse</code>; saved with the Save button)</span>
      </label>

      <div className="grid gap-2 rounded-md border border-line p-3 text-[12px] sm:grid-cols-2">
        <div className="flex items-center gap-1.5">
          {status?.node.ok ? <CheckCircle2 size={13} className="text-ok" /> : <XCircle size={13} className="text-err" />}
          Node.js {status?.node.version || 'not found'} <span className="text-faint">(needs 22.19+, 24.11+ or 26+)</span>
        </div>
        <div className="flex items-center gap-1.5">
          {status?.npx ? <CheckCircle2 size={13} className="text-ok" /> : <XCircle size={13} className="text-err" />} npx {status?.npx ? 'available' : 'not found'}
        </div>
        {status?.live && <div className="sm:col-span-2 text-faint">Server: {status.live.status}{status.live.tools ? ` · ${status.live.tools} tools` : ''}{status.live.error ? ` · ${status.live.error}` : ''}</div>}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1">
          <span className="text-[12px] text-muted">REA version (npm rea-agents)</span>
          <input className={inputCls} value={rev.version} onChange={(e) => set({ version: e.target.value })} placeholder="6.4.0 or latest" />
        </label>
        <label className="block space-y-1">
          <span className="text-[12px] text-muted">Native analysis provider (REA_ANALYSIS_PROVIDER)</span>
          <select className={inputCls} value={rev.provider} onChange={(e) => set({ provider: e.target.value as AppConfig['reverse']['provider'] })}>
            <option value="auto">auto (the single available provider)</option><option value="ghidra">Ghidra</option><option value="hopper">Hopper</option><option value="ida">IDA</option>
          </select>
        </label>
        <label className="block space-y-1">
          <span className="text-[12px] text-muted">Tool schemas (REA_MCP_INPUT_SCHEMA_PROFILE)</span>
          <select className={inputCls} value={rev.schemaProfile} onChange={(e) => set({ schemaProfile: e.target.value as AppConfig['reverse']['schemaProfile'] })}>
            <option value="compact">compact - smaller prompts, best for local models</option><option value="full">full - complete schemas</option>
          </select>
        </label>
        <label className="block space-y-1">
          <span className="text-[12px] text-muted">Tool call timeout (seconds)</span>
          <input className={inputCls} type="number" min={60} max={7200} value={rev.timeout} onChange={(e) => set({ timeout: Number(e.target.value) })} />
        </label>
      </div>
      <label className="block space-y-1">
        <span className="text-[12px] text-muted">Extra environment for REA (one per line, e.g. REA_GHIDRA_STARTUP_TIMEOUT_MS=600000, REA_JEB_MCP_URL=...)</span>
        <textarea className={`${inputCls} min-h-[60px] font-mono text-[12px]`} value={envText} onChange={(e) => setEnvText(e.target.value)} />
      </label>

      <div className="space-y-2">
        <button type="button" onClick={runDoctor} disabled={doctor.running || !status?.npx} className="inline-flex items-center gap-1.5 rounded border border-line px-3 py-1.5 text-[12px] hover:border-accent/60 disabled:opacity-40">
          {doctor.running ? <Loader2 size={12} className="spin" /> : <Stethoscope size={12} />} Check readiness (rea doctor)
        </button>
        <p className="text-[11px] text-faint">Read-only diagnosis of Node, analysis engines and providers. The first run downloads rea-agents with npx.</p>
        {doctor.output !== undefined && (
          <pre className={cx('max-h-64 overflow-auto whitespace-pre-wrap rounded border px-2 py-1.5 text-[11px]', doctor.ok ? 'border-ok/40 text-muted' : 'border-err/40 text-err')}>
            {doctor.command ? `$ ${doctor.command}\n\n` : ''}{doctor.output}
          </pre>
        )}
      </div>
    </div>
  );
}
