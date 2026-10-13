import { useEffect, useState } from 'react';
import { Download, Loader2, PlugZap, X } from 'lucide-react';
import type { AppConfig, Tier } from '../types';
import { useT, type Strings } from '../i18n';
import { api } from '../api';
import { TIERS, cx } from '../util';
import { McpSettings } from './McpSettings';
import { ModsManager } from './ModsManager';
import { ReverseSettings } from './ReverseSettings';
import { SkillsManager } from './SkillsManager';
import { TierChip, TierIcon } from './TierIcon';

export type SettingsTab = 'llm' | 'tiers' | 'workspace' | 'bitbucket' | 'flow' | 'context' | 'agent' | 'mcp' | 'skills' | 'mods' | 'reverse';
type Tab = SettingsTab;

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

function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="flex items-start gap-2">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 accent-[var(--accent)]" />
      <span>{label}{hint && <span className="block text-[11px] text-faint">{hint}</span>}</span>
    </label>
  );
}

function Num({ label, value, onChange, min, max, step = 1, hint }: { label: string; value: number; onChange: (v: number) => void; min: number; max: number; step?: number; hint?: string }) {
  return (
    <Field label={label} hint={hint}>
      <input className={inputCls} type="number" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </Field>
  );
}

const rulesToText = (rules: AppConfig['permissions']['bashRules']) => rules.map((r) => `${r.pattern} = ${r.action}`).join('\n');
const textToRules = (text: string): AppConfig['permissions']['bashRules'] =>
  text.split('\n').map((l) => /^(.+?)\s*=\s*(allow|ask|deny)\s*$/.exec(l.trim())).filter(Boolean).map((m) => ({ pattern: m![1].trim(), action: m![2] as 'allow' | 'ask' | 'deny' }));

export function SettingsModal({ initial, onClose, onSaved, initialTab = 'llm', directory = '', onRunCommand }: {
  initial: AppConfig; onClose: () => void; onSaved: (c: AppConfig) => void; initialTab?: Tab; directory?: string; onRunCommand?: (text: string) => void;
}) {
  const t = useT();
  const [cfg, setCfg] = useState<AppConfig>(initial);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [test, setTest] = useState<{ ok: boolean; message: string; models?: string[] } | null>(null);
  const [testing, setTesting] = useState(false);
  const [nexusPath, setNexusPath] = useState(initial.nexusDbPath || '');
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [rulesText, setRulesText] = useState(rulesToText(initial.permissions.bashRules || []));
  const [verifyText, setVerifyText] = useState((initial.verify?.commands || []).join('\n'));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const setLlm = (patch: Partial<AppConfig['llm']>) => setCfg((c) => ({ ...c, llm: { ...c.llm, ...patch } }));
  const setFlow = (patch: Partial<AppConfig['flow']>) => setCfg((c) => ({ ...c, flow: { ...c.flow, ...patch } }));
  const setCtx = (patch: Partial<AppConfig['context']>) => setCfg((c) => ({ ...c, context: { ...c.context, ...patch } }));
  const setAgent = (patch: Partial<AppConfig['agent']>) => setCfg((c) => ({ ...c, agent: { ...c.agent, ...patch } }));
  const setPerm = (patch: Partial<AppConfig['permissions']>) => setCfg((c) => ({ ...c, permissions: { ...c.permissions, ...patch } }));
  const setBb = (patch: Partial<AppConfig['bitbucket']>) => setCfg((c) => ({ ...c, bitbucket: { ...c.bitbucket, ...patch } }));
  const [bbTest, setBbTest] = useState<{ ok: boolean; message: string } | null>(null);
  const runBbTest = async () => {
    setBbTest(null);
    try {
      const r = await api.testBitbucket(cfg.bitbucket, cfg.workspace);
      setBbTest({ ok: r.ok, message: r.ok ? `OK: ${r.kind} ${r.name}` : r.message || 'Failed' });
    } catch (e: any) {
      setBbTest({ ok: false, message: e.message });
    }
  };
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
      const saved = await api.saveConfig({
        ...cfg,
        permissions: { ...cfg.permissions, bashRules: textToRules(rulesText) },
        verify: { ...cfg.verify, commands: verifyText.split('\n').map((x) => x.trim()).filter(Boolean) },
      });
      onSaved(saved);
      onClose();
    } catch (e: any) {
      setNotice({ ok: false, text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const models = test?.models || [];
  const tabs: [Tab, string][] = [['llm', t.tabLlm], ['tiers', t.tabTiers], ['workspace', t.tabWorkspace], ['bitbucket', t.tabBitbucket], ['flow', t.tabFlow],
    ['context', t.tabContext], ['agent', t.tabAgentCfg], ['mcp', t.tabMcp], ['skills', t.tabSkills], ['mods', t.tabMods], ['reverse', t.tabReverse]];
  const runAndClose = onRunCommand ? (text: string) => { onRunCommand(text); onClose(); } : undefined;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal aria-label={t.settings} className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-line bg-panel shadow-2xl">
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
              <Field label={t.responseLanguage}>
                <input className={inputCls} value={cfg.llm.responseLanguage} onChange={(e) => setLlm({ responseLanguage: e.target.value })} placeholder="English" />
              </Field>
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
              {(['edit', 'bash', 'webfetch', 'mcp', 'mods'] as const).map((k) => (
                <Field key={k} label={{ edit: t.permEdit, bash: t.permBash, webfetch: t.permWeb, mcp: t.permMcp, mods: t.permMods }[k]}>
                  <div className="flex gap-1.5">
                    {(['allow', 'ask', 'deny'] as const).map((v) => (
                      <button key={v} type="button" onClick={() => setPerm({ [k]: v })}
                        className={cx('rounded border px-3 py-1 text-[12px]', cfg.permissions[k] === v ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg')}>
                        {v === 'allow' ? t.permAllow : v === 'ask' ? t.permAsk : t.permDeny}
                      </button>
                    ))}
                  </div>
                </Field>
              ))}
              <Toggle label={t.autoModeDefault} checked={cfg.autoMode} onChange={(v) => setCfg((c) => ({ ...c, autoMode: v }))} />
              <Field label={t.projectsRoot}>
                <input className={inputCls} value={cfg.projectsRoot} onChange={(e) => setCfg((c) => ({ ...c, projectsRoot: e.target.value }))} spellCheck={false} />
              </Field>
              <Field label={t.shell}>
                <select className={inputCls} value={['auto', 'bash', 'powershell', 'cmd'].includes(cfg.shell) ? cfg.shell : 'auto'} onChange={(e) => setCfg((c) => ({ ...c, shell: e.target.value }))}>
                  <option value="auto">{t.shellAuto}</option>
                  <option value="bash">bash / Git Bash</option>
                  <option value="powershell">PowerShell</option>
                  <option value="cmd">cmd.exe</option>
                </select>
              </Field>
              <Field label={t.bashRules}>
                <textarea className={`${inputCls} min-h-[140px] text-[12px]`} value={rulesText} onChange={(e) => setRulesText(e.target.value)} spellCheck={false} />
              </Field>
              <Field label={t.verifyCommands}>
                <textarea className={`${inputCls} min-h-[64px] text-[12px]`} value={verifyText} onChange={(e) => setVerifyText(e.target.value)} placeholder="npm run build&#10;pytest -q" spellCheck={false} />
              </Field>
            </>
          )}

          {tab === 'bitbucket' && (
            <>
              <Field label={t.bbKind}>
                <select className={inputCls} value={cfg.bitbucket.kind} onChange={(e) => setBb({ kind: e.target.value as 'server' | 'cloud' })}>
                  <option value="server">{t.bbServer}</option>
                  <option value="cloud">{t.bbCloud}</option>
                </select>
              </Field>
              {cfg.bitbucket.kind === 'server' && (
                <Field label={t.bbBaseUrl} hint={t.bbBaseUrlHint}>
                  <input className={inputCls} value={cfg.bitbucket.baseUrl} onChange={(e) => setBb({ baseUrl: e.target.value })} placeholder="https://bitbucket.company.com" />
                </Field>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t.bbProject} hint={t.bbAutoHint}><input className={inputCls} value={cfg.bitbucket.project} onChange={(e) => setBb({ project: e.target.value })} /></Field>
                <Field label={t.bbRepo} hint={t.bbAutoHint}><input className={inputCls} value={cfg.bitbucket.repo} onChange={(e) => setBb({ repo: e.target.value })} /></Field>
                <Field label={t.bbUsername}><input className={inputCls} value={cfg.bitbucket.username} onChange={(e) => setBb({ username: e.target.value })} autoComplete="off" /></Field>
                <Field label={t.bbToken}><input className={inputCls} type="password" value={cfg.bitbucket.token} onChange={(e) => setBb({ token: e.target.value })} autoComplete="off" /></Field>
                <Field label={t.bbTarget}><input className={inputCls} value={cfg.bitbucket.targetBranch} onChange={(e) => setBb({ targetBranch: e.target.value })} placeholder="develop" /></Field>
                <Field label={t.bbPrefix}><input className={inputCls} value={cfg.bitbucket.branchPrefix} onChange={(e) => setBb({ branchPrefix: e.target.value })} /></Field>
                <Field label={t.bbRemote}><input className={inputCls} value={cfg.bitbucket.remote} onChange={(e) => setBb({ remote: e.target.value })} /></Field>
                <Field label={t.bbReviewers}>
                  <input className={inputCls} value={cfg.bitbucket.reviewers.join(', ')} onChange={(e) => setBb({ reviewers: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} />
                </Field>
              </div>
              <Toggle label={t.bbAutoPr} checked={cfg.bitbucket.autoPrOnFlow} onChange={(v) => setBb({ autoPrOnFlow: v })} />
              <Toggle label={t.bbVerifySsl} checked={cfg.bitbucket.verifySsl} onChange={(v) => setBb({ verifySsl: v })} />
              <div className="space-y-2">
                <button type="button" onClick={runBbTest} className="inline-flex items-center gap-1.5 rounded border border-line px-3 py-1.5 hover:border-accent/60">
                  <PlugZap size={13} /> {t.bbTest}
                </button>
                {bbTest && <div className={cx('rounded border px-3 py-2 text-[12px]', bbTest.ok ? 'border-ok/40 text-ok' : 'border-err/40 text-err')}>{bbTest.message}</div>}
              </div>
            </>
          )}

          {tab === 'flow' && (
            <>
              <Toggle label={t.requireApproval} checked={cfg.flow.requirePlanApproval} onChange={(v) => setFlow({ requirePlanApproval: v })} />
              <Toggle label={t.hierarchical} checked={cfg.flow.hierarchical} onChange={(v) => setFlow({ hierarchical: v })} />
              <Toggle label={t.splitLarge} checked={cfg.flow.splitLargeTasks} onChange={(v) => setFlow({ splitLargeTasks: v })} />
              <Toggle label={t.mergeSmall} checked={cfg.flow.mergeSmallTasks} onChange={(v) => setFlow({ mergeSmallTasks: v })} />
              <Toggle label={t.autoVerify} checked={cfg.flow.autoVerify} onChange={(v) => setFlow({ autoVerify: v })} />
              <Toggle label={t.fastAcceptLight} checked={cfg.flow.fastAcceptLight} onChange={(v) => setFlow({ fastAcceptLight: v })} />
              <Toggle label={t.escalate} checked={cfg.flow.escalateOnFail} onChange={(v) => setFlow({ escalateOnFail: v })} />
              <Toggle label={t.autoResume} checked={cfg.flow.autoResume} onChange={(v) => setFlow({ autoResume: v })} />
              <Toggle label={t.writeReport} checked={cfg.flow.writeReport} onChange={(v) => setFlow({ writeReport: v })} />
              <Toggle label={t.gitCommit} checked={cfg.flow.gitCommit} onChange={(v) => setFlow({ gitCommit: v })} />
              <div className="grid gap-3 sm:grid-cols-2">
                <Num label={t.maxParallel} hint={t.maxParallelHint} value={cfg.flow.maxParallel} min={1} max={8} onChange={(v) => setFlow({ maxParallel: v })} />
                <Num label={t.maxReturnRounds} value={cfg.flow.maxReturnRounds} min={0} max={5} onChange={(v) => setFlow({ maxReturnRounds: v })} />
                <Num label={t.autoFixRetries} value={cfg.flow.autoFixRetries} min={0} max={5} onChange={(v) => setFlow({ autoFixRetries: v })} />
                <Num label={t.integrationRounds} value={cfg.flow.integrationRounds} min={0} max={5} onChange={(v) => setFlow({ integrationRounds: v })} />
                <Num label={t.taskTimeout} value={cfg.flow.taskTimeoutMinutes} min={1} max={600} onChange={(v) => setFlow({ taskTimeoutMinutes: v })} />
                <Num label={t.tokenBudget} value={cfg.flow.tokenBudget} min={0} max={1_000_000_000} step={100000} onChange={(v) => setFlow({ tokenBudget: v })} />
                <Num label={t.planSteps} value={cfg.flow.planSteps} min={0} max={30} onChange={(v) => setFlow({ planSteps: v })} />
                <Num label={t.reviewSteps} value={cfg.flow.reviewSteps} min={0} max={30} onChange={(v) => setFlow({ reviewSteps: v })} />
              </div>
            </>
          )}

          {tab === 'context' && (
            <>
              <Toggle label={t.repoMap} checked={cfg.context.repoMap} onChange={(v) => setCtx({ repoMap: v })} />
              <div className="grid gap-3 sm:grid-cols-2">
                <Num label={t.repoMapChars} value={cfg.context.repoMapChars} min={500} max={40000} step={500} onChange={(v) => setCtx({ repoMapChars: v })} />
                <Num label={t.toolOutputMax} value={cfg.context.toolOutputMaxChars} min={1000} max={200000} step={1000} onChange={(v) => setCtx({ toolOutputMaxChars: v })} />
                <Num label={t.pruneAt} value={cfg.context.pruneAt} min={0.1} max={0.95} step={0.05} onChange={(v) => setCtx({ pruneAt: v })} />
                <Num label={t.compactAt} value={cfg.context.compactAt} min={0.2} max={0.95} step={0.05} onChange={(v) => setCtx({ compactAt: v })} />
                <Num label={t.keepRecent} value={cfg.context.keepRecentShare} min={0.1} max={0.6} step={0.05} onChange={(v) => setCtx({ keepRecentShare: v })} />
              </div>
            </>
          )}

          {tab === 'agent' && (
            <>
              <Toggle label={t.readBeforeEdit} checked={cfg.agent.requireReadBeforeEdit} onChange={(v) => setAgent({ requireReadBeforeEdit: v })} />
              <div className="grid gap-3 sm:grid-cols-2">
                <Num label={t.maxRetries} value={cfg.agent.maxRetries} min={0} max={10} onChange={(v) => setAgent({ maxRetries: v })} />
                <Num label={t.doomLoop} value={cfg.agent.doomLoopThreshold} min={2} max={10} onChange={(v) => setAgent({ doomLoopThreshold: v })} />
                <Num label={t.goalMax} value={cfg.agent.goalMaxContinuations} min={1} max={100} onChange={(v) => setAgent({ goalMaxContinuations: v })} />
              </div>
            </>
          )}
          {tab === 'mcp' && <McpSettings cfg={cfg} setCfg={setCfg} directory={directory || cfg.workspace} />}
          {tab === 'skills' && <SkillsManager directory={directory || cfg.workspace} onRunCommand={runAndClose} />}
          {tab === 'mods' && <ModsManager onRunCommand={runAndClose} />}
          {tab === 'reverse' && <ReverseSettings cfg={cfg} setCfg={setCfg} />}
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
