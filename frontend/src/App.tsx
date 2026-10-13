import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ClipboardList, Database, Menu, PanelLeft, PanelRight, SquareTerminal, X } from 'lucide-react';
import { api } from './api';
import { applyEvent } from './sessionReducer';
import { I18nContext, STRINGS } from './i18n';
import type { AgentMode, AppConfig, CommandInfo, JournalEntry, ModToast, PermissionMode, ProcInfo, ServerEvent, Session, SessionSummary } from './types';
import { agentColor, cx, fmtTokens } from './util';
import { clearAttention, notify, requestNotifyPermission } from './notify';
import { ActivityView } from './components/ActivityView';
import { CommandPalette, type PaletteItem } from './components/CommandPalette';
import { COMMANDS, Composer } from './components/Composer';
import { DataView } from './components/DataView';
import { FileViewer } from './components/FileViewer';
import { Markdown } from './components/Markdown';
import { McpChip } from './components/McpChip';
import { ModBand, ModStatusLine, Toasts } from './components/ModUi';
import { PermissionBar } from './components/PermissionBar';
import { ProjectDialog } from './components/ProjectDialog';
import { ProgressBar } from './components/ProgressBar';
import { QuestionBar } from './components/QuestionBar';
import { SettingsModal, type SettingsTab } from './components/SettingsModal';
import { SidePanel, type SideTab } from './components/SidePanel';
import { Sidebar } from './components/Sidebar';
import { TerminalPanel } from './components/TerminalPanel';
import { ToolActions } from './components/ToolPartView';
import { TodoList } from './components/TodoList';
import { Transcript } from './components/Transcript';

const INIT_PROMPT =
  'Analyze this project and create or update AGENTS.md at the root: stack in one line, the exact build/test/run/lint commands, ' +
  '3-7 non-obvious conventions an agent would otherwise get wrong. Keep it under 40 lines. Read the code first.';

const EDIT_TOOLS = new Set(['write', 'edit', 'multiedit', 'apply_patch', 'bash']);
const MAX_TERM_OUTPUT = 400_000;

/* Live process output: chunks carry their absolute offset, so a chunk already in the snapshot is not shown twice. */
function appendOutput(p: ProcInfo, offset: number, chunk: string): ProcInfo {
  const end = offset + chunk.length;
  if (end <= p.size) return p;
  const fresh = offset < p.size ? chunk.slice(p.size - offset) : chunk;
  const output = (p.output || '') + fresh;
  return { ...p, output: output.length > MAX_TERM_OUTPUT ? output.slice(-MAX_TERM_OUTPUT) : output, size: end };
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [llmOk, setLlmOk] = useState<boolean | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [agent, setAgent] = useState<AgentMode>('build');
  const [panelOpen, setPanelOpen] = useState(false);
  const [sideTab, setSideTab] = useState<SideTab>('agents');
  const [focusRunId, setFocusRunId] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [projectOpen, setProjectOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [help, setHelp] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dismissedFlow, setDismissedFlow] = useState<string | null>(null);
  const [queues, setQueues] = useState<Record<string, string[]>>({});
  const [inject, setInject] = useState<{ text: string; nonce: number } | null>(null);
  const [liveJournal, setLiveJournal] = useState<JournalEntry[]>([]);
  const [filesVersion, setFilesVersion] = useState(0);
  const [stuck, setStuck] = useState(true);
  const [activityOpen, setActivityOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('llm');
  const [toasts, setToasts] = useState<ModToast[]>([]);
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const [extVersion, setExtVersion] = useState(0);
  // terminal, files, preview, data
  const [procs, setProcs] = useState<ProcInfo[]>([]);
  const [termOpen, setTermOpen] = useState(false);
  const [termHeight, setTermHeight] = useState(() => Math.round(Math.min(320, window.innerHeight * 0.35)));
  const [termCwd, setTermCwd] = useState('');
  const [termSel, setTermSel] = useState<string | null>(null);
  const [fileView, setFileView] = useState<{ path: string; line?: number | null; preview?: boolean } | null>(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [previewVersion, setPreviewVersion] = useState(0);
  const [panelWide, setPanelWide] = useState(false);
  const [dataOpen, setDataOpen] = useState(false);
  const [dataFile, setDataFile] = useState<string | null>(null);
  const [dataSignal, setDataSignal] = useState<{ kind: 'dataset' | 'view'; id: string; nonce: number } | null>(null);
  const [draftMode, setDraftMode] = useState<PermissionMode | null>(null);
  const autoOpenedFor = useRef<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const wasBusy = useRef(false);

  const lang = 'en';
  const t = STRINGS.en;
  const tRef = useRef(t);
  tRef.current = t;

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
    const onFocus = () => clearAttention();
    const askOnce = () => {
      requestNotifyPermission();
      window.removeEventListener('pointerdown', askOnce);
    };
    window.addEventListener('focus', onFocus);
    window.addEventListener('pointerdown', askOnce);
    return () => {
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('pointerdown', askOnce);
    };
  }, []);

  // Open session: full snapshot + live events.
  useEffect(() => {
    setLiveJournal([]);
    setProcs([]);
    setTermSel(null);
    setFileView(null);
    setPreviewUrl('');
    setDataOpen(false);
    setDataFile(null);
    setDataSignal(null);
    if (!currentId) {
      setSession(null);
      return;
    }
    const loadTerminal = () => api.terminal(currentId).then((r) => {
      if (!alive) return;
      setProcs(r.processes);
      setTermCwd(r.cwd);
    }).catch(() => {});
    let alive = true;
    // Events are buffered while a snapshot loads, then only those newer than it are applied:
    // no duplicated stream deltas after a reconnect.
    let seq = -1;
    let buffer: ServerEvent[] | null = [];
    const apply = (e: ServerEvent) => {
      if (e.seq !== undefined) {
        if (e.seq <= seq) return;
        seq = e.seq;
      }
      setSession((s) => (s ? applyEvent(s, e) : s));
    };
    const load = () => {
      buffer = buffer || [];
      return api.session(currentId).then((s) => {
        if (!alive) return;
        seq = s.seq ?? -1;
        setSession((prev) => {
          // Keep worker transcripts already loaded: the snapshot carries none.
          if (!prev || prev.id !== s.id) return s;
          const loaded = Object.fromEntries(prev.runs.filter((r) => r.messages).map((r) => [r.id, r.messages]));
          return { ...s, runs: s.runs.map((r) => (loaded[r.id] ? { ...r, messages: loaded[r.id] } : r)) };
        });
        setAgent(s.agent);
        const pending = buffer || [];
        buffer = null;
        pending.forEach(apply);
      }).catch((e) => {
        setError(e.message);
        const pending = buffer || [];
        buffer = null;
        pending.forEach(apply);
      });
    };
    load();
    loadTerminal();
    let filesTimer: ReturnType<typeof setTimeout> | null = null;
    const stop = api.subscribe(currentId, (e) => {
      if (e.type === 'hello' || e.type === 'session.reload') {
        load();
        loadTerminal();
        return;
      }
      if (e.type === 'terminal.updated') {
        const p = e.process;
        setProcs((list) => {
          const old = list.find((x) => x.id === p.id);
          if (!old) return [...list, { ...p, output: '', size: 0 }];
          if (p.urls.length > old.urls.length) setPreviewVersion((v) => v + 1);
          return list.map((x) => (x.id === p.id ? { ...x, ...p, output: x.output, size: x.size } : x));
        });
        if (p.owner === 'user' && p.kind === 'terminal' && p.status === 'running') setTermOpen(true);
        if (p.status !== 'running') setFilesVersion((v) => v + 1);
        return;
      }
      if (e.type === 'terminal.output') {
        setProcs((list) => list.map((x) => (x.id === e.procId ? appendOutput(x, e.offset, e.chunk) : x)));
        return;
      }
      if (e.type === 'terminal.removed') {
        setProcs((list) => list.filter((x) => x.id !== e.procId));
        return;
      }
      if (e.type === 'terminal.cwd') {
        setTermCwd(e.cwd);
        return;
      }
      if (e.type === 'files.changed') {
        setFilesVersion((v) => v + 1);
        return;
      }
      if (e.type === 'ui.show') {
        if (e.kind === 'url' && e.url) {
          setPreviewUrl(e.url);
          setPreviewVersion((v) => v + 1);
          setSideTab('preview');
          setPanelWide(true);
          setPanelOpen(true);
        } else if (e.kind === 'file' && e.path) {
          setFileView({ path: e.path, line: e.line, preview: e.preview });
        }
        return;
      }
      if (e.type === 'data.dataset') setDataSignal({ kind: 'dataset', id: e.dataset.id, nonce: Date.now() });
      if (e.type === 'data.view') {
        setDataSignal({ kind: 'view', id: e.datasetId, nonce: Date.now() });
        return;
      }
      if (e.type === 'journal') {
        setLiveJournal((j) => [...j.slice(-600), e.entry]);
        if (e.entry.kind === 'tool' && EDIT_TOOLS.has(e.entry.tool) && !filesTimer) {
          filesTimer = setTimeout(() => { filesTimer = null; setFilesVersion((v) => v + 1); }, 1500);
        }
        return;
      }
      if (e.type === 'mods.toast') {
        setToasts((list) => [...list.slice(-5), { id: Date.now() + Math.random(), mod: e.mod, text: e.text, kind: e.kind }]);
        return;
      }
      if (e.type === 'mods.changed' || e.type === 'skills.changed') {
        setExtVersion((v) => v + 1);
        return;
      }
      if (buffer) buffer.push(e);
      else apply(e);
      const tt = tRef.current;
      if (e.type === 'permission.asked') notify(tt.notifyPermission, `${e.permission.agent}: ${e.permission.tool} ${e.permission.input?.command || e.permission.input?.path || ''}`);
      if (e.type === 'question.asked') notify(tt.notifyQuestion, e.question.questions[0]?.question || '');
      if (e.type === 'flow.updated' && e.flow.isFinished) notify(tt.notifyDone, e.flow.title);
      if (e.type === 'session.updated') {
        setSessions((list) => list.map((x) => (x.id === e.session.id ? e.session : x)).sort((a, b) => b.updatedAt - a.updatedAt));
      } else if (e.type === 'status') {
        setSessions((list) => list.map((x) => (x.id === currentId ? { ...x, busy: e.busy } : x)));
        if (!e.busy) setFilesVersion((v) => v + 1);
      }
    });
    stick.current = true;
    return () => {
      alive = false;
      if (filesTimer) clearTimeout(filesTimer);
      stop();
    };
  }, [currentId]);

  const busy = Boolean(session?.busy);
  const directory = session?.directory || config?.workspace || '';

  // Slash commands the server answers: /mod, /reverse, mod commands, skills.
  useEffect(() => {
    api.commands(directory).then(setCommands).catch(() => {});
  }, [directory, extVersion]);

  const openSettings = (tab: SettingsTab = 'llm') => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  };
  const dismissToast = useCallback((id: number) => setToasts((list) => list.filter((x) => x.id !== id)), []);
  const flow = session?.flow ?? null;
  const runs = session?.runs ?? [];

  // Turn finished while away: notify.
  useEffect(() => {
    if (wasBusy.current && !busy && session && !session.flow?.isFinished) notify(t.notifyDone, session.title);
    wasBusy.current = busy;
  }, [busy]); // eslint-disable-line react-hooks/exhaustive-deps

  // savvy-progress: the agents panel opens itself once per flow when delegation starts.
  useEffect(() => {
    if (!flow || flow.phase !== 'delegate' || autoOpenedFor.current === flow.id) return;
    autoOpenedFor.current = flow.id;
    if (window.innerWidth >= 1200) {
      setPanelOpen(true);
      setSideTab('agents');
    }
  }, [flow?.phase, flow?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the transcript pinned to the bottom while the user has not scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [session]);

  // Only a user scroll unpins; layout changes (queue, permission bar) never do.
  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) {
      stick.current = true;
      setStuck(true);
    }
  };
  const onUserScroll = (e: React.WheelEvent | React.TouchEvent | React.KeyboardEvent) => {
    if ('deltaY' in e && e.deltaY > 0) return;
    if ('key' in e && !['PageUp', 'ArrowUp', 'Home'].includes(e.key)) return;
    requestAnimationFrame(() => {
      const el = scroller.current;
      if (el && el.scrollHeight - el.scrollTop - el.clientHeight >= 80) {
        stick.current = false;
        setStuck(false);
      }
    });
  };
  useEffect(() => {
    const el = scroller.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, []);
  const jumpToLatest = () => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    stick.current = true;
    setStuck(true);
  };

  const startProject = async (directory: string, auto: boolean) => {
    try {
      const s = await api.createSession(agent, directory, auto);
      setProjectOpen(false);
      setSessions((list) => [s, ...list]);
      setSession(s);
      setCurrentId(s.id);
      setNavOpen(false);
      api.config().then(setConfig).catch(() => {});
    } catch (e: any) {
      setError(e.message);
    }
  };

  const newSession = async (mode: AgentMode = agent): Promise<Session | null> => {
    try {
      const s = await api.createSession(mode, '', null, draftMode);
      setDraftMode(null);
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

  const ensureSession = async (mode: AgentMode): Promise<string | null> => currentId || (await newSession(mode))?.id || null;

  const sendNow = async (sid: string, text: string, mode: AgentMode) => {
    // Optimistic busy BEFORE the request: a fast turn may finish (status event) before the POST returns.
    setSession((s) => (s && s.id === sid ? { ...s, busy: true } : s));
    try {
      await api.send(sid, text, mode);
    } catch (e: any) {
      setError(e.message);
      api.session(sid).then((fresh) => setSession((s) => (s && s.id === sid ? { ...s, busy: fresh.busy } : s))).catch(() => {});
    }
  };

  const send = async (text: string, mode: AgentMode) => {
    setError(null);
    setHelp(false);
    stick.current = true;
    if (mode !== agent) setAgent(mode);
    const sid = await ensureSession(mode);
    if (!sid) return;
    if (busy && sid === currentId) {
      setQueues((q) => ({ ...q, [sid]: [...(q[sid] || []), text] }));
      return;
    }
    await sendNow(sid, text, mode);
  };

  // Message queue: next prompt goes out as soon as the agent is free.
  const queued = currentId ? queues[currentId] || [] : [];
  useEffect(() => {
    if (!currentId || busy || !session || session.id !== currentId || !queued.length) return;
    if (session.pendingPermissions.length || session.pendingQuestions?.length) return;
    const [next, ...rest] = queued;
    setQueues((q) => ({ ...q, [currentId]: rest }));
    sendNow(currentId, next, next.startsWith('/flow') ? 'flow' : agent);
  }, [busy, queued.length, currentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const changeAgent = (a: AgentMode) => {
    setAgent(a);
    if (currentId) api.patchSession(currentId, { agent: a }).catch(() => {});
  };

  const loadRun = useCallback((runId: string) => {
    if (!currentId) return;
    api.run(currentId, runId).then((run) => {
      setSession((s) => (s ? { ...s, runs: s.runs.map((r) => (r.id === runId ? { ...r, messages: run.messages || [] } : r)) } : s));
    }).catch(() => {});
  }, [currentId]);

  const downloadReport = async () => {
    if (!currentId) return;
    const md = await api.report(currentId);
    const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `report-${currentId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const fork = async (messageId?: string) => {
    if (!currentId) return;
    try {
      const s = await api.fork(currentId, messageId);
      setSessions((list) => [s, ...list]);
      setCurrentId(s.id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const globalMode: PermissionMode = config?.permissionMode || (config?.autoMode ? 'auto' : 'ask');
  const permMode: PermissionMode = !currentId ? draftMode || globalMode : session?.permissionMode
    || (session?.autoMode === true ? 'auto' : session?.autoMode === false ? 'ask' : globalMode);
  // A session's mode is its own (Settings holds the default); before the first session, it applies to the next one.
  const setPermMode = (mode: PermissionMode) => {
    if (!currentId) {
      setDraftMode(mode);
      return;
    }
    const sid = currentId;
    setSession((s) => (s && s.id === sid ? { ...s, permissionMode: mode, autoMode: mode === 'auto' } : s));
    api.patchSession(sid, { permissionMode: mode }).catch((e) => setError(e.message));
  };
  const toggleAuto = () => setPermMode(permMode === 'auto' ? 'ask' : 'auto');

  const runShell = async (command: string) => {
    setError(null);
    const sid = await ensureSession(agent);
    if (!sid) return;
    stick.current = true;
    try {
      await api.shell(sid, command);
    } catch (e: any) {
      setError(e.message);
    }
  };
  const openFile = useCallback((path: string, line?: number | null) => setFileView({ path, line }), []);
  const openDataset = useCallback((id: string) => {
    setDataOpen(true);
    setDataSignal({ kind: 'dataset', id, nonce: Date.now() });
  }, []);
  const openDataFile = (path: string) => {
    setFileView(null);
    setDataFile(path);
    setDataOpen(true);
  };
  const toolActions = useMemo(() => ({ openFile, openDataset }), [openFile, openDataset]);

  const onCommand = async (name: string, arg?: string) => {
    setError(null);
    if (name === 'new') setProjectOpen(true);
    else if (name === 'pr') {
      setSideTab('git');
      setPanelOpen(true);
    }
    else if (name === 'build' || name === 'plan') changeAgent(name);
    else if (name === 'flow') arg ? send(`/flow ${arg}`, 'flow') : changeAgent('flow');
    else if (name === 'agents') {
      setPanelOpen(true);
      setSideTab('agents');
    } else if (name === 'settings') openSettings('llm');
    else if (name === 'activity') currentId && setActivityOpen(true);
    else if (name === 'mcp' || name === 'skills' || name === 'mods') openSettings(name);
    else if (name === 'help') setHelp(true);
    else if (name === 'init') send(INIT_PROMPT, 'build');
    else if (name === 'report') downloadReport();
    else if (name === 'fork') fork();
    else if (name === 'auto') toggleAuto();
    else if (name === 'terminal') currentId ? setTermOpen(true) : setError('Open a session first.');
    else if (name === 'files' || name === 'preview') {
      setSideTab(name);
      if (name === 'preview') setPanelWide(true);
      setPanelOpen(true);
    } else if (name === 'data') currentId ? setDataOpen(true) : setError('Open a session first.');
    else if (name === 'undo' && currentId) {
      try {
        const r = await api.undo(currentId);
        setInject({ text: r.text, nonce: Date.now() });
        setInfo(`${t.undone} ${r.files.join(', ') || '-'}`);
        setFilesVersion((v) => v + 1);
      } catch (e: any) {
        setError(e.message);
      }
    } else if (name === 'compact' && currentId) {
      api.compact(currentId).catch((e) => setError(e.message));
    } else if (name === 'goal') {
      const sid = await ensureSession('build');
      if (!sid) return;
      changeAgent('build');
      api.goal(sid, arg || null).then((r) => setSession((s) => (s ? { ...s, goal: r.goal } : s))).catch((e) => setError(e.message));
    }
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
    setSession((s) => (s ? { ...s, busy: true } : s));
    api.approveFlow(currentId).catch((e) => {
      setError(e.message);
      setSession((s) => (s ? { ...s, busy: false } : s));
    });
  };

  const openRun = (runId: string) => {
    setFocusRunId(runId);
    setSideTab('agents');
    setPanelOpen(true);
  };

  // Global shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); setPaletteOpen((o) => !o); }
      else if (mod && e.key.toLowerCase() === 'b') { e.preventDefault(); setNavCollapsed((c) => !c); }
      else if (mod && e.key === '.') { e.preventDefault(); setPanelOpen((o) => !o); }
      else if (e.ctrlKey && (e.key === '`' || e.code === 'Backquote')) { e.preventDefault(); if (currentId) setTermOpen((o) => !o); }
      else if (e.altKey && e.key.toLowerCase() === 'n') { e.preventDefault(); setProjectOpen(true); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const paletteItems: PaletteItem[] = useMemo(() => {
    const items: PaletteItem[] = COMMANDS.filter((c) => !c.arg).map((c) => ({
      id: `cmd-${c.name}`, group: 'cmd', label: `/${c.name}`, hint: t[c.desc], run: () => onCommand(c.name),
    }));
    items.push({ id: 'cmd-flow', group: 'cmd', label: '/flow …', hint: t.cmdFlow, run: () => setInject({ text: '/flow ', nonce: Date.now() }) });
    items.push({ id: 'cmd-goal', group: 'cmd', label: '/goal …', hint: t.cmdGoal, run: () => setInject({ text: '/goal ', nonce: Date.now() }) });
    (['agents', 'plan', 'files', 'preview', 'git', 'trace', 'mods'] as SideTab[]).forEach((k) => items.push({
      id: `tab-${k}`, group: 'panel', label: { agents: t.tabAgents, plan: t.tabPlan, files: t.tabFiles, preview: t.tabPreview, git: t.tabGit, trace: t.tabTrace, mods: t.tabMods }[k],
      run: () => { setSideTab(k); setPanelOpen(true); },
    }));
    commands.filter((c) => !COMMANDS.some((x) => x.name === c.name)).forEach((c) => items.push({
      id: `srv-${c.name}`, group: 'cmd', label: `/${c.name}${c.argumentHint ? ' …' : ''}`, hint: c.description,
      run: () => (c.argumentHint ? setInject({ text: `/${c.name} `, nonce: Date.now() }) : send(`/${c.name}`, agent)),
    }));
    sessions.forEach((s) => items.push({ id: `ses-${s.id}`, group: 'session', label: s.title, hint: s.directory, run: () => setCurrentId(s.id) }));
    return items;
  }, [sessions, t, commands]); // eslint-disable-line react-hooks/exhaustive-deps

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
        <div className={cx('fixed inset-y-0 left-0 z-40 w-[260px] shrink-0 transition-transform md:static md:z-auto md:translate-x-0',
          navOpen ? 'translate-x-0' : '-translate-x-full', navCollapsed && 'md:hidden')}>
          <Sidebar
            sessions={sessions}
            currentId={currentId}
            onSelect={(id) => { setCurrentId(id); setNavOpen(false); }}
            onNew={() => setProjectOpen(true)}
            onDelete={deleteSession}
            onSettings={() => openSettings()}
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
            <button type="button" className="hidden text-faint hover:text-fg md:block" onClick={() => setNavCollapsed((c) => !c)} title="Ctrl+B"><PanelLeft size={15} /></button>
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: agentColor(agent) }} />
            <div className="min-w-0 flex-1">
              <div className="truncate font-semibold">{session?.title || t.appName}</div>
              {session && <div className="truncate text-[11px] text-faint">{session.directory}</div>}
            </div>
            {totalTokens > 0 && <span className="hidden shrink-0 text-[11px] tabular-nums text-faint sm:inline">{fmtTokens(totalTokens)} tok</span>}
            <button type="button" onClick={() => setPaletteOpen(true)} className="hidden shrink-0 rounded border border-line px-2 py-1 text-[11px] text-faint hover:text-fg sm:block">Ctrl K</button>
            {session && (
              <button type="button" onClick={() => setTermOpen((o) => !o)} title={t.terminalHint} aria-pressed={termOpen}
                className={cx('inline-flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 text-[12px]', termOpen ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}>
                <SquareTerminal size={13} /> <span className="hidden lg:inline">{t.terminal}</span>
                {procs.some((p) => p.status === 'running') && <span className="pulse h-1.5 w-1.5 rounded-full bg-ok" />}
              </button>
            )}
            {session && config?.data?.enabled !== false && (
              <button type="button" onClick={() => setDataOpen(true)} title={t.dataHint}
                className="inline-flex shrink-0 items-center gap-1.5 rounded border border-line px-2 py-1 text-[12px] text-muted hover:text-fg">
                <Database size={13} /> <span className="hidden lg:inline">{t.data}</span>
                {(session.datasets?.length || 0) > 0 && <span className="rounded bg-elev px-1 text-[10px] text-faint">{session.datasets!.length}</span>}
              </button>
            )}
            {session && (
              <button type="button" onClick={() => setActivityOpen(true)} title={t.activityHint}
                className="inline-flex shrink-0 items-center gap-1.5 rounded border border-line px-2 py-1 text-[12px] text-muted hover:text-fg">
                <ClipboardList size={13} /> <span className="hidden sm:inline">{t.activity}</span>
              </button>
            )}
            <button
              type="button"
              onClick={() => setPanelOpen((o) => !o)}
              aria-pressed={panelOpen}
              title="Ctrl+."
              className={cx('inline-flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 text-[12px]', panelOpen ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg')}
            >
              <PanelRight size={13} /> {t.agents}
              {runningRuns > 0 && <span className="pulse rounded bg-accent px-1 text-[10px] font-bold text-bg">{runningRuns}</span>}
            </button>
          </header>

          <div className="relative min-h-0 flex-1">
            <div ref={scroller} onScroll={onScroll} onWheel={onUserScroll} onTouchMove={onUserScroll} onKeyDown={onUserScroll} tabIndex={-1} className="h-full overflow-y-auto outline-none">
              <div className="mx-auto w-full max-w-3xl px-4 py-5">
                {empty ? (
                  <div className="mt-[10vh] space-y-5 text-center">
                    <pre className="inline-block text-left text-[11px] leading-[1.2] text-accent" aria-hidden>{`  ██████╗  ██████╗ ██╗███╗   ██╗ ██████╗
  ██╔══██╗██╔═══██╗██║████╗  ██║██╔════╝
  ██║  ██║██║   ██║██║██╔██╗ ██║██║  ███╗
  ██║  ██║██║   ██║██║██║╚██╗██║██║   ██║
  ██████╔╝╚██████╔╝██║██║ ╚████║╚██████╔╝
  ╚═════╝  ╚═════╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝ .AI
                        S U P E R C O D E`}</pre>
                    <h1 className="text-base font-bold">{t.emptyTitle}</h1>
                    <ul className="mx-auto max-w-lg space-y-1.5 text-left text-[12px] text-muted">
                      <li><span style={{ color: 'var(--build)' }}>▸</span> {t.emptyHint1}</li>
                      <li><span style={{ color: 'var(--flow)' }}>▸</span> {t.emptyHint2}</li>
                      <li><span className="text-faint">▸</span> {t.emptyHint3}</li>
                      <li><span className="text-faint">▸</span> <Markdown text={t.shortcutsText} /></li>
                    </ul>
                    {llmOk === false && (
                      <button type="button" onClick={() => openSettings()} className="rounded border border-err/50 px-3 py-1.5 text-err hover:bg-err/10">
                        {t.configureLlm} · {t.llmOffline}
                      </button>
                    )}
                  </div>
                ) : (
                  <ToolActions.Provider value={toolActions}>
                  <Transcript
                    messages={session!.messages}
                    flow={flow}
                    busy={busy}
                    onApprove={approve}
                    onOpenRun={openRun}
                    onRetry={(text, a) => send(text, (a === 'plan' || a === 'flow' ? a : 'build') as AgentMode)}
                    onEdit={(text) => onCommand('undo').then(() => setInject({ text, nonce: Date.now() }))}
                    onFork={(id) => fork(id)}
                  />
                  </ToolActions.Provider>
                )}
              </div>
            </div>
            {!stuck && !empty && (
              <button type="button" onClick={jumpToLatest}
                className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1 rounded-full border border-line bg-elev px-3 py-1 text-[12px] text-muted shadow-lg hover:text-fg">
                <ArrowDown size={12} /> {t.jumpLatest}
              </button>
            )}
          </div>

          <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pb-4 pt-1">
            {error && (
              <div className="flex items-start gap-2 rounded border border-err/40 bg-err/5 px-3 py-1.5 text-[12px] text-err">
                <span className="min-w-0 flex-1 break-words">{error}</span>
                <button type="button" onClick={() => setError(null)} aria-label={t.close}><X size={13} /></button>
              </div>
            )}
            {info && (
              <div className="flex items-start gap-2 rounded border border-ok/40 bg-ok/5 px-3 py-1.5 text-[12px] text-ok">
                <span className="min-w-0 flex-1 break-words">{info}</span>
                <button type="button" onClick={() => setInfo(null)} aria-label={t.close}><X size={13} /></button>
              </div>
            )}
            {help && (
              <div className="relative rounded-md border border-line bg-panel px-3 py-2 text-[12px]">
                <button type="button" className="absolute right-2 top-2 text-faint hover:text-fg" onClick={() => setHelp(false)} aria-label={t.close}><X size={13} /></button>
                <Markdown text={`${t.helpText}\n\n**${t.shortcuts}**: ${t.shortcutsText}`} />
              </div>
            )}
            {session && <TodoList todos={session.todos} />}
            {showProgress && (
              <ProgressBar
                flow={flow}
                runs={runs}
                panelOpen={panelOpen && sideTab === 'agents'}
                onTogglePanel={() => { setSideTab('agents'); setPanelOpen((o) => !(o && sideTab === 'agents')); }}
                onApprove={approve}
                onDismiss={() => flow && setDismissedFlow(flow.id)}
                busy={busy}
              />
            )}
            {session && (
              <QuestionBar
                question={session.pendingQuestions?.[0]}
                onAnswer={(qid, answers) => currentId && api.answer(currentId, qid, answers).catch((e) => setError(e.message))}
              />
            )}
            {session && (
              <PermissionBar
                permissions={session.pendingPermissions}
                onReply={(pid, r) => currentId && api.permission(currentId, pid, r).catch((e) => setError(e.message))}
              />
            )}
            {session && <ModBand ui={session.modUi} onRun={(cmd) => send(cmd, agent)} disabled={busy} />}
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
              inject={inject}
              queued={queued}
              onRemoveQueued={(i) => currentId && setQueues((q) => ({ ...q, [currentId]: (q[currentId] || []).filter((_, k) => k !== i) }))}
              contextTokens={session?.contextTokens || 0}
              contextWindow={config?.llm.contextWindow || 0}
              totalTokens={totalTokens}
              compactions={session?.compactions || 0}
              goal={session?.goal}
              mode={permMode}
              onModeChange={setPermMode}
              onShell={runShell}
              onClearGoal={() => currentId && api.goal(currentId, null).then(() => setSession((s) => (s ? { ...s, goal: null } : s)))}
              serverCommands={commands}
              footerExtra={(
                <McpChip sessionId={currentId} directory={directory} disabled={session?.mcpDisabled || []} version={extVersion}
                  onChange={(list) => setSession((s) => (s ? { ...s, mcpDisabled: list } : s))} onManage={() => openSettings('mcp')} />
              )}
            />
            {session && <ModStatusLine ui={session.modUi} />}
          </div>
          {termOpen && currentId && (
            <>
              <div role="separator" aria-orientation="horizontal" title="Drag to resize" className="h-1 shrink-0 cursor-row-resize bg-line/40 hover:bg-accent/50"
                onMouseDown={(e) => {
                  e.preventDefault();
                  const move = (ev: MouseEvent) => setTermHeight(Math.max(120, Math.min(window.innerHeight * 0.75, window.innerHeight - ev.clientY)));
                  const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
                  window.addEventListener('mousemove', move);
                  window.addEventListener('mouseup', up);
                }} />
              <TerminalPanel sessionId={currentId} procs={procs} cwd={termCwd || directory} selected={termSel} onSelect={setTermSel} height={termHeight}
                onClose={() => setTermOpen(false)}
                onPreview={(url) => { setPreviewUrl(url); setPreviewVersion((v) => v + 1); setSideTab('preview'); setPanelWide(true); setPanelOpen(true); }} />
            </>
          )}
        </main>

        {/* savvy-progress agents, plan board, files, trace */}
        {panelOpen && (
          <div className={cx('fixed inset-y-0 right-0 z-40 border-l border-line shadow-2xl xl:static xl:z-auto xl:shrink-0 xl:shadow-none',
            panelWide && (sideTab === 'preview' || sideTab === 'files') ? 'w-[min(760px,100vw)] xl:w-[min(760px,45vw)]' : 'w-[min(400px,100vw)]')}>
            <SidePanel
              tab={sideTab}
              onTab={setSideTab}
              onClose={() => setPanelOpen(false)}
              sessionId={currentId}
              runs={runs}
              flow={flow}
              focusRunId={focusRunId}
              onOpenRun={openRun}
              onLoadRun={loadRun}
              busy={busy}
              filesVersion={filesVersion}
              liveJournal={liveJournal}
              onOpenSettings={() => openSettings('bitbucket')}
              modUi={session?.modUi}
              onManageMods={() => openSettings('mods')}
              onOpenFile={openFile}
              selectedFile={fileView?.path || null}
              preview={{ url: previewUrl, onUrl: setPreviewUrl, wide: panelWide, onToggleWide: () => setPanelWide((w) => !w), version: previewVersion + filesVersion,
                localOnly: config?.network?.localOnly !== false }}
            />
          </div>
        )}

        {projectOpen && <ProjectDialog defaultAuto={Boolean(config?.autoMode)} onClose={() => setProjectOpen(false)} onStart={startProject} />}

        {paletteOpen && <CommandPalette items={paletteItems} onClose={() => setPaletteOpen(false)} />}

        {activityOpen && currentId && <ActivityView sessionId={currentId} busy={busy} onClose={() => setActivityOpen(false)} />}

        {dataOpen && currentId && session && (
          <DataView sessionId={currentId} messages={session.messages} busy={busy} signal={dataSignal} initialFile={dataFile}
            onSend={(text) => send(text, agent === 'flow' ? 'build' : agent)} onClose={() => { setDataOpen(false); setDataFile(null); }}
            onOpenSettings={() => openSettings('mcp')} />
        )}

        {fileView && currentId && (
          <FileViewer sessionId={currentId} path={fileView.path} line={fileView.line} startInPreview={fileView.preview} onClose={() => setFileView(null)}
            onMention={(p) => { setInject({ text: `@${p} `, nonce: Date.now() }); setFileView(null); }}
            onOpenData={openDataFile} onDeleted={() => setFilesVersion((v) => v + 1)} />
        )}

        {/* Approvals stay reachable above the full-screen views. */}
        {session && (dataOpen || fileView) && (session.pendingPermissions.length > 0 || (session.pendingQuestions?.length || 0) > 0) && (
          <div className="fixed inset-x-0 bottom-3 z-[60] mx-auto w-[min(720px,calc(100vw-24px))] space-y-2 rounded-lg bg-bg shadow-2xl">
            <QuestionBar question={session.pendingQuestions?.[0]}
              onAnswer={(qid, answers) => currentId && api.answer(currentId, qid, answers).catch((e) => setError(e.message))} />
            <PermissionBar permissions={session.pendingPermissions}
              onReply={(pid, r) => currentId && api.permission(currentId, pid, r).catch((e) => setError(e.message))} />
          </div>
        )}

        <Toasts toasts={toasts} onDismiss={dismissToast} />

        {settingsOpen && config && (
          <SettingsModal
            initial={config}
            initialTab={settingsTab}
            directory={directory}
            onRunCommand={currentId ? (text) => send(text, 'build') : undefined}
            onClose={() => { setSettingsOpen(false); setExtVersion((v) => v + 1); }}
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
