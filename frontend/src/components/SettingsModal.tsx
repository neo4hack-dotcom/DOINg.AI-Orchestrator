import { useEffect, useState } from 'react';
import { Download, Loader2, PlugZap, X } from 'lucide-react';
import type { AppConfig, Tier } from '../types';
import { useT, type Strings } from '../i18n';
import { api } from '../api';
import { TIERS, cx } from '../util';
import { TierChip, TierIcon } from './TierIcon';

type Tab = 'llm' | 'tiers' | 'workspace' | 'flow';

const TIER_DESC: Record<Tier, keyof Strings> = {
  fable: 'tierFable',
  heavy: 'tierHeavy',
  careful: 'tierCareful',
  medium: 'tierMedium',
  light: 'tierLight',
};

const inputCls = 'w-full rounded border border-line bg-bg px-2.5 py-1.5 text-fg outline-none focus:border-accent placeholder:text-faint';

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-[12px] text-muted">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

export function SettingsModal({ initial, onClose, onSaved }: { initial: AppConfig; onClose: () => void; onSaved: (c: AppConfig) => void }) {
  const t = useT();
  const [cfg, setCfg] = useState<AppConfig>(initial);
  const [tab, setTab] = useState<Tab>('llm');
  const [test, setTest] = useState<{ ok: boolean; message: string; models?: string[] } | null>(null);
  const [testing, setTesting] = useState(false);
  const [nexusPath, setNexusPath] = useState(initial.nexusDbPath || '');
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const setLlm = (patch: Partial<AppConfig['llm']>) => setCfg((c) => ({ ...c, llm: { ...c.llm, ...patch } }));
  const setFlow = (patch: Partial<AppConfig['flow']>) => setCfg((c) => ({ ...c, flow: { ...c.flow, ...patch } }));
  const setTier = (tier: Tier, patch: Partial<AppConfig['tiers'][Tier]>) =>
    setCfg((c) => ({ ...c, tiers: { ...c.tiers, [tier]: { ...c.tiers[tier], ...patch } } }));

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api.testLlm(cfg.llm));
    } catch (e: any) {
      setTest({ ok: false, message: e.message });
    } finally {
      setTesting(false);
    }
  };

  const importNexus = async () => {
    setNotice(null);
    try {
      const res = await api.importNexus(nexusPath);
      setCfg((c) => ({ ...c, llm: res.config.llm, nexusDbPath: res.config.nexusDbPath }));
      setNotice({ ok: true, text: `${t.imported} ${res.source}` });
      onSaved(res.config);
    } catch (e: any) {
      setNotice({ ok: false, text: e.message });
    }
  };

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const saved = await api.saveConfig(cfg);
      onSaved(saved);
      onClose();
    } catch (e: any) {
      setNotice({ ok: false, text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const models = test?.models || [];
  const tabs: [Tab, string][] = [['llm', t.tabLlm], ['tiers', t.tabTiers], ['workspace', t.tabWorkspace], ['flow', t.tabFlow]];

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal aria-label={t.settings} className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-line bg-panel shadow-2xl">
        <div className="flex items-center border-b border-line px-4 py-3">
          <span className="font-bold">{t.settings}</span>
          <button type="button" onClick={onClose} className="ml-auto text-faint hover:text-fg" aria-label={t.close}><X size={16} /></button>
        </div>
        <div className="flex gap-4 overflow-x-auto border-b border-line px-4">
          {tabs.map(([k, label]) => (
            <button key={k} type="button" onClick={() => setTab(k)} className={cx('shrink-0 border-b-2 py-2 text-[12px]', tab === k ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg')}>
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {tab === 'llm' && (
            <>
              <div className="rounded-md border border-accent/30 bg-accent/5 p-3">
                <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-accent"><Download size={13} /> {t.importNexus}</div>
                <div className="flex gap-2">
                  <input className={inputCls} value={nexusPath} onChange={(e) => setNexusPath(e.target.value)} placeholder="../NEXUS.AI" />
                  <button type="button" onClick={importNexus} className="shrink-0 rounded bg-accent px-3 font-semibold text-bg hover:brightness-110">OK</button>
                </div>
                <p className="mt-1 text-[11px] text-faint">{t.importNexusHint}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t.provider}>
                  <select className={inputCls} value={cfg.llm.provider} onChange={(e) => setLlm({ provider: e.target.value as AppConfig['llm']['provider'] })}>
                    <option value="ollama">Ollama</option>
                    <option value="openai_compatible">OpenAI-compatible (LM Studio, vLLM, llama.cpp…)</option>
                    <option value="n8n">n8n webhook</option>
                  </select>
                </Field>
                <Field label={t.baseUrl}>
                  <input className={inputCls} value={cfg.llm.baseUrl} onChange={(e) => setLlm({ baseUrl: e.target.value })} placeholder="http://localhost:11434" />
                </Field>
                <Field label={t.model}>
                  <input className={inputCls} list="llm-models" value={cfg.llm.model} onChange={(e) => setLlm({ model: e.target.value })} />
                </Field>
                <Field label={t.apiKey}>
                  <input className={inputCls} type="password" value={cfg.llm.apiKey} onChange={(e) => setLlm({ apiKey: e.target.value })} autoComplete="off" />
                </Field>
                <Field label={t.toolMode}>
                  <select className={inputCls} value={cfg.llm.toolMode} onChange={(e) => setLlm({ toolMode: e.target.value as AppConfig['llm']['toolMode'] })}>
                    <option value="auto">{t.toolAuto}</option>
                    <option value="native">{t.toolNative}</option>
                    <option value="prompt">{t.toolPrompt}</option>
                  </select>
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label={t.contextWindow}>
                    <input className={inputCls} type="number" min={2048} step={1024} value={cfg.llm.contextWindow} onChange={(e) => setLlm({ contextWindow: Number(e.target.value) })} />
                  </Field>
                  <Field label={t.temperature}>
                    <input className={inputCls} type="number" min={0} max={2} step={0.1} value={cfg.llm.temperature} onChange={(e) => setLlm({ temperature: Number(e.target.value) })} />
                  </Field>
                </div>
              </div>
              <Field label={t.systemPrompt}>
                <textarea className={`${inputCls} min-h-[64px]`} value={cfg.llm.systemPrompt} onChange={(e) => setLlm({ systemPrompt: e.target.value })} />
              </Field>
              <div className="space-y-2">
                <button type="button" onClick={runTest} disabled={testing} className="inline-flex items-center gap-1.5 rounded border border-line px-3 py-1.5 hover:border-accent/60">
                  {testing ? <Loader2 size={13} className="spin" /> : <PlugZap size={13} />} {testing ? t.testing : t.test}
                </button>
                {test && (
                  <div className={cx('rounded border px-3 py-2 text-[12px]', test.ok ? 'border-ok/40 text-ok' : 'border-err/40 text-err')}>
                    {test.message}
                    {models.length > 0 && (
                      <>
                        <div className="mt-1 text-faint">{t.clickModel}</div>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {models.map((m) => (
                            <button key={m} type="button" onClick={() => setLlm({ model: m })} className={cx('rounded border px-1.5 py-0.5', cfg.llm.model === m ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}>
                              {m}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            </>
          )}

          {tab === 'tiers' && (
            <>
              <Field label={t.orchestratorModel} hint={t.orchestratorHint}>
                <input className={inputCls} list="llm-models" value={cfg.orchestratorModel} onChange={(e) => setCfg((c) => ({ ...c, orchestratorModel: e.target.value }))} placeholder={cfg.llm.model} />
              </Field>
              <p className="text-[11px] text-faint">{t.tierHint}</p>
              <div className="divide-y divide-line rounded-md border border-line">
                {TIERS.map((tier) => (
                  <div key={tier} className="grid items-center gap-2 px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_180px_110px]">
                    <div className="flex min-w-0 items-center gap-2">
                      <TierIcon tier={tier} />
                      <div className="min-w-0">
                        <TierChip tier={tier} />
                        <div className="truncate text-[11px] text-faint">{t[TIER_DESC[tier]]}</div>
                      </div>
                    </div>
                    <input className={inputCls} list="llm-models" value={cfg.tiers[tier].model} onChange={(e) => setTier(tier, { model: e.target.value })} placeholder={cfg.llm.model} aria-label={`${tier} ${t.model}`} />
                    <select className={inputCls} value={cfg.tiers[tier].effort} onChange={(e) => setTier(tier, { effort: e.target.value as AppConfig['tiers'][Tier]['effort'] })} aria-label={`${tier} ${t.effort}`}>
                      {['low', 'medium', 'high', 'xhigh'].map((x) => <option key={x} value={x}>{x}</option>)}
                    </select>
                  </div>
                ))}
              </div>
            </>
          )}

          {tab === 'workspace' && (
            <>
              <Field label={t.workspace} hint={t.workspaceHint}>
                <input className={inputCls} value={cfg.workspace} onChange={(e) => setCfg((c) => ({ ...c, workspace: e.target.value }))} />
              </Field>
              {(['edit', 'bash'] as const).map((k) => (
                <Field key={k} label={k === 'edit' ? t.permEdit : t.permBash}>
                  <div className="flex gap-1.5">
                    {(['allow', 'ask', 'deny'] as const).map((v) => (
                      <button key={v} type="button" onClick={() => setCfg((c) => ({ ...c, permissions: { ...c.permissions, [k]: v } }))}
                        className={cx('rounded border px-3 py-1 text-[12px]', cfg.permissions[k] === v ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg')}>
                        {v === 'allow' ? t.permAllow : v === 'ask' ? t.permAsk : t.permDeny}
                      </button>
                    ))}
                  </div>
                </Field>
              ))}
              <Field label={t.language}>
                <select className={inputCls} value={cfg.language} onChange={(e) => setCfg((c) => ({ ...c, language: e.target.value as AppConfig['language'] }))}>
                  <option value="auto">auto</option>
                  <option value="fr">Français</option>
                  <option value="en">English</option>
                </select>
              </Field>
            </>
          )}

          {tab === 'flow' && (
            <>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={cfg.flow.requirePlanApproval} onChange={(e) => setFlow({ requirePlanApproval: e.target.checked })} className="accent-[var(--accent)]" />
                {t.requireApproval}
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={cfg.flow.gitCommit} onChange={(e) => setFlow({ gitCommit: e.target.checked })} className="accent-[var(--accent)]" />
                {t.gitCommit}
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t.maxParallel} hint={t.maxParallelHint}>
                  <input className={inputCls} type="number" min={1} max={8} value={cfg.flow.maxParallel} onChange={(e) => setFlow({ maxParallel: Number(e.target.value) })} />
                </Field>
                <Field label={t.maxReturnRounds}>
                  <input className={inputCls} type="number" min={0} max={5} value={cfg.flow.maxReturnRounds} onChange={(e) => setFlow({ maxReturnRounds: Number(e.target.value) })} />
                </Field>
                <Field label={t.planSteps}>
                  <input className={inputCls} type="number" min={0} max={30} value={cfg.flow.planSteps} onChange={(e) => setFlow({ planSteps: Number(e.target.value) })} />
                </Field>
                <Field label={t.reviewSteps}>
                  <input className={inputCls} type="number" min={0} max={30} value={cfg.flow.reviewSteps} onChange={(e) => setFlow({ reviewSteps: Number(e.target.value) })} />
                </Field>
              </div>
            </>
          )}
          <datalist id="llm-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
        </div>

        <div className="flex items-center gap-3 border-t border-line px-4 py-3">
          {notice && <span className={cx('min-w-0 truncate text-[12px]', notice.ok ? 'text-ok' : 'text-err')}>{notice.text}</span>}
          <div className="ml-auto flex gap-2">
            <button type="button" onClick={onClose} className="rounded border border-line px-3 py-1.5 text-muted hover:text-fg">{t.cancel}</button>
            <button type="button" onClick={save} disabled={saving} className="rounded bg-accent px-3 py-1.5 font-semibold text-bg hover:brightness-110 disabled:opacity-50">{t.save}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
