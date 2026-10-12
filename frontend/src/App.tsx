import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Menu, Users, X } from 'lucide-react';
import { api } from './api';
import { applyEvent } from './sessionReducer';
import { I18nContext, STRINGS, detectLang } from './i18n';
import type { AgentMode, AppConfig, Session, SessionSummary } from './types';
import { agentColor, cx, fmtTokens } from './util';
import { AgentsPanel } from './components/AgentsPanel';
import { Composer } from './components/Composer';
import { Markdown } from './components/Markdown';
import { PermissionBar } from './components/PermissionBar';
import { ProgressBar } from './components/ProgressBar';
import { SettingsModal } from './components/SettingsModal';
import { Sidebar } from './components/Sidebar';
import { TodoList } from './components/TodoList';
import { Transcript } from './components/Transcript';

const INIT_PROMPT =
  'Analyze this project and create or update AGENTS.md at the root: stack in one line, the exact build/test/run/lint commands, ' +
  '3-7 non-obvious conventions an agent would otherwise get wrong. Keep it under 40 lines. Read the code first.';

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [llmOk, setLlmOk] = useState<boolean | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [agent, setAgent] = useState<AgentMode>('build');
  const [panelOpen, setPanelOpen] = useState(false);
  const [focusRunId, setFocusRunId] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dismissedFlow, setDismissedFlow] = useState<string | null>(null);
  const autoOpenedFor = useRef<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const lang = detectLang(config?.language);
  const t = STRINGS[lang];

  const refreshSessions = useCallback(() => api.sessions().then(setSessions).catch(() => {}), []);

  useEffect(() => {
    api.config().then((c) => {
      setConfig(c);
      api.testLlm().then((r) => setLlmOk(r.ok)).catch(() => setLlmOk(false));
    }).catch((e) => setError(e.message));
    api.sessions().then((list) => {
      setSessions(list);
      if (list[0]) setCurrentId(list[0].id);
    }).catch((e) => setError(e.message));
  }, []);

  // Open session: full snapshot + live events.
  useEffect(() => {
    if (!currentId) {
      setSession(null);
      return;
    }
    let alive = true;
    const load = () => api.session(currentId).then((s) => {
      if (!alive) return;
      setSession(s);
      setAgent(s.agent);
    }).catch((e) => setError(e.message));
    load();
    const stop = api.subscribe(currentId, (e) => {
      if (e.type === 'hello') {
        load();
        return;
      }
      setSession((s) => (s ? applyEvent(s, e) : s));
      if (e.type === 'session.updated') {
        setSessions((list) => list.map((x) => (x.id === e.session.id ? e.session : x)).sort((a, b) => b.updatedAt - a.updatedAt));
      } else if (e.type === 'status') {
        setSessions((list) => list.map((x) => (x.id === currentId ? { ...x, busy: e.busy } : x)));
      }
    });
    stick.current = true;
    return () => {
      alive = false;
      stop();
    };
  }, [currentId]);

  // savvy-progress: the agents panel opens itself once per flow when delegation starts.
  useEffect(() => {
    const flow = session?.flow;
    if (!flow || flow.phase !== 'delegate' || autoOpenedFor.current === flow.id) return;
    autoOpenedFor.current = flow.id;
    if (window.innerWidth >= 1200) setPanelOpen(true);
  }, [session?.flow?.phase, session?.flow?.id]);

  // Keep the transcript pinned to the bottom while the user has not scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [session]);

  const onScroll = () => {
    const el = scroller.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const newSession = async (mode: AgentMode = agent): Promise<Session | null> => {
    try {
      const s = await api.createSession(mode);
      setSessions((list) => [s, ...list]);
      setSession(s);
      setCurrentId(s.id);
      setNavOpen(false);
      return s;
    } catch (e: any) {
      setError(e.message);
      return null;
    }
  };

  const send = async (text: string, mode: AgentMode) => {
    setError(null);
    setHelp(false);
    stick.current = true;
    let sid = currentId;
    if (!sid) {
      const s = await newSession(mode);
      if (!s) return;
      sid = s.id;
    }
    if (mode !== agent) setAgent(mode);
    try {
      await api.send(sid, text, mode);
      setSession((s) => (s ? { ...s, busy: true } : s));
    } catch (e: any) {
      setError(e.message);
    }
  };

  const changeAgent = (a: AgentMode) => {
    setAgent(a);
    if (currentId) api.patchSession(currentId, { agent: a }).catch(() => {});
  };

  const onCommand = (name: string) => {
    if (name === 'new') newSession();
    else if (name === 'build' || name === 'plan' || name === 'flow') changeAgent(name);
    else if (name === 'agents') setPanelOpen((o) => !o);
    else if (name === 'settings') setSettingsOpen(true);
    else if (name === 'help') setHelp(true);
    else if (name === 'init') send(INIT_PROMPT, 'build');
  };

  const deleteSession = async (id: string) => {
    if (!window.confirm(t.confirmDelete)) return;
    await api.deleteSession(id).catch(() => {});
    const rest = sessions.filter((s) => s.id !== id);
    setSessions(rest);
    if (id === currentId) setCurrentId(rest[0]?.id ?? null);
  };

  const approve = () => {
    if (!currentId) return;
    api.approveFlow(currentId).then(() => setSession((s) => (s ? { ...s, busy: true } : s))).catch((e) => setError(e.message));
  };

  const openRun = (runId: string) => {
    setFocusRunId(runId);
    setPanelOpen(true);
  };

  const busy = Boolean(session?.busy);
  const flow = session?.flow ?? null;
  const runs = session?.runs ?? [];
  const runningRuns = runs.filter((r) => r.status === 'running').length;
  const showProgress = Boolean((flow && dismissedFlow !== flow.id) || runningRuns);
  const answering = Boolean(flow && !flow.isFinished && flow.awaiting && agent === 'flow');
  const model = useMemo(() => {
    if (!config) return '';
    if (agent === 'flow') return `orchestrator · ${config.orchestratorModel || config.llm.model}`;
    return config.llm.model;
  }, [config, agent]);
  const totalTokens = (session?.tokens?.input || 0) + (session?.tokens?.output || 0);
  const empty = !session || session.messages.length === 0;

  return (
    <I18nContext.Provider value={t}>
      <div className="flex h-full overflow-hidden">
        {/* sessions */}
        <div className={cx('fixed inset-y-0 left-0 z-40 w-[260px] transition-transform md:static md:z-auto md:translate-x-0', navOpen ? 'translate-x-0' : '-translate-x-full')}>
          <Sidebar
            sessions={sessions}
            currentId={currentId}
            onSelect={(id) => { setCurrentId(id); setNavOpen(false); }}
            onNew={() => newSession()}
            onDelete={deleteSession}
            onSettings={() => setSettingsOpen(true)}
            config={config}
            llmOk={llmOk}
            lang={lang}
          />
        </div>
        {navOpen && <div className="fixed inset-0 z-30 bg-black/50 md:hidden" onClick={() => setNavOpen(false)} />}

        {/* main */}
        <main className="flex min-w-0 flex-1 flex-col">
          <header className="flex min-w-0 items-center gap-3 border-b border-line px-4 py-2.5">
            <button type="button" className="text-muted md:hidden" onClick={() => setNavOpen(true)} aria-label={t.sessions}><Menu size={16} /></button>
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: agentColor(agent) }} />
            <div className="min-w-0 flex-1">
              <div className="truncate font-semibold">{session?.title || t.appName}</div>
              {session && <div className="truncate text-[11px] text-faint">{session.directory}</div>}
            </div>
            {totalTokens > 0 && <span className="hidden shrink-0 text-[11px] tabular-nums text-faint sm:inline">{fmtTokens(totalTokens)} tok</span>}
            <button
              type="button"
              onClick={() => setPanelOpen((o) => !o)}
              aria-pressed={panelOpen}
              className={cx('inline-flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 text-[12px]', panelOpen ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}
            >
              <Users size={13} /> {t.agents}
              {runningRuns > 0 && <span className="pulse rounded bg-accent px-1 text-[10px] font-bold text-bg">{runningRuns}</span>}
            </button>
          </header>

          <div ref={scroller} onScroll={onScroll} className="flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-3xl px-4 py-5">
              {empty ? (
                <div className="mt-[12vh] space-y-5 text-center">
                  <pre className="inline-block text-left text-[11px] leading-[1.2] text-accent" aria-hidden>{`  ██████╗  ██████╗ ██╗███╗   ██╗ ██████╗
  ██╔══██╗██╔═══██╗██║████╗  ██║██╔════╝
  ██║  ██║██║   ██║██║██╔██╗ ██║██║  ███╗
  ██║  ██║██║   ██║██║██║╚██╗██║██║   ██║
  ██████╔╝╚██████╔╝██║██║ ╚████║╚██████╔╝
  ╚═════╝  ╚═════╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝ .AI`}</pre>
                  <h1 className="text-base font-bold">{t.emptyTitle}</h1>
                  <ul className="mx-auto max-w-lg space-y-1.5 text-left text-[12px] text-muted">
                    <li><span style={{ color: 'var(--build)' }}>▸</span> {t.emptyHint1}</li>
                    <li><span style={{ color: 'var(--flow)' }}>▸</span> {t.emptyHint2}</li>
                    <li><span className="text-faint">▸</span> {t.emptyHint3}</li>
                  </ul>
                  {llmOk === false && (
                    <button type="button" onClick={() => setSettingsOpen(true)} className="rounded border border-err/50 px-3 py-1.5 text-err hover:bg-err/10">
                      {t.configureLlm} · {t.llmOffline}
                    </button>
                  )}
                </div>
              ) : (
                <Transcript messages={session!.messages} flow={flow} busy={busy} onApprove={approve} onOpenRun={openRun} />
              )}
            </div>
          </div>

          <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pb-4">
            {error && (
              <div className="flex items-start gap-2 rounded border border-err/40 bg-err/5 px-3 py-1.5 text-[12px] text-err">
                <span className="min-w-0 flex-1 break-words">{error}</span>
                <button type="button" onClick={() => setError(null)} aria-label={t.close}><X size={13} /></button>
              </div>
            )}
            {help && (
              <div className="relative rounded-md border border-line bg-panel px-3 py-2 text-[12px]">
                <button type="button" className="absolute right-2 top-2 text-faint hover:text-fg" onClick={() => setHelp(false)} aria-label={t.close}><X size={13} /></button>
                <Markdown text={t.helpText} />
              </div>
            )}
            {session && <TodoList todos={session.todos} />}
            {showProgress && (
              <ProgressBar
                flow={flow}
                runs={runs}
                panelOpen={panelOpen}
                onTogglePanel={() => setPanelOpen((o) => !o)}
                onApprove={approve}
                onDismiss={() => flow && setDismissedFlow(flow.id)}
                busy={busy}
              />
            )}
            {session && (
              <PermissionBar
                permissions={session.pendingPermissions}
                onReply={(pid, r) => currentId && api.permission(currentId, pid, r).catch((e) => setError(e.message))}
              />
            )}
            <Composer
              sessionId={currentId}
              agent={agent}
              onAgentChange={changeAgent}
              busy={busy}
              onSend={send}
              onAbort={() => currentId && api.abort(currentId)}
              onCommand={onCommand}
              model={model}
              answering={answering}
            />
          </div>
        </main>

        {/* savvy-progress agents panel */}
        {panelOpen && (
          <div className="fixed inset-y-0 right-0 z-40 w-[min(380px,100vw)] border-l border-line shadow-2xl xl:static xl:z-auto xl:shadow-none">
            <AgentsPanel runs={runs} flow={flow} focusRunId={focusRunId} onClose={() => setPanelOpen(false)} />
          </div>
        )}

        {settingsOpen && config && (
          <SettingsModal
            initial={config}
            onClose={() => setSettingsOpen(false)}
            onSaved={(c) => {
              setConfig(c);
              setLlmOk(null);
              api.testLlm().then((r) => setLlmOk(r.ok)).catch(() => setLlmOk(false));
              refreshSessions();
            }}
          />
        )}
      </div>
    </I18nContext.Provider>
  );
}
