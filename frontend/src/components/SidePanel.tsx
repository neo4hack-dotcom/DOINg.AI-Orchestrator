import { X } from 'lucide-react';
import type { AgentRun, Flow, JournalEntry, ModUi } from '../types';
import { useT } from '../i18n';
import { cx } from '../util';
import { AgentsPanel } from './AgentsPanel';
import { FilesPanel } from './FilesPanel';
import { GitPanel } from './GitPanel';
import { ModPanels } from './ModUi';
import { PlanBoard } from './PlanBoard';
import { TracePanel } from './TracePanel';

export type SideTab = 'agents' | 'plan' | 'files' | 'git' | 'trace' | 'mods';

/* Right panel: savvy-progress agents, the plan board, changed files and the trace. */
export function SidePanel({ tab, onTab, onClose, sessionId, runs, flow, focusRunId, onOpenRun, onLoadRun, busy, filesVersion, liveJournal, onOpenSettings,
  modUi, onManageMods }: {
  tab: SideTab;
  onTab: (t: SideTab) => void;
  onClose: () => void;
  sessionId: string | null;
  runs: AgentRun[];
  flow: Flow | null;
  focusRunId: string | null;
  onOpenRun: (id: string) => void;
  onLoadRun: (id: string) => void;
  busy: boolean;
  filesVersion: number;
  liveJournal: JournalEntry[];
  onOpenSettings: () => void;
  modUi?: ModUi;
  onManageMods: () => void;
}) {
  const t = useT();
  const running = runs.filter((r) => r.status === 'running').length;
  const tabs: [SideTab, string, number | null][] = [
    ['agents', t.tabAgents, running || null],
    ['plan', t.tabPlan, flow ? flow.tasks.length : null],
    ['files', t.tabFiles, null],
    ['git', t.tabGit, null],
    ['trace', t.tabTrace, null],
    ['mods', t.tabMods, Object.keys(modUi?.panels || {}).length || null],
  ];
  return (
    <aside className="flex h-full w-full flex-col bg-bg">
      <div className="flex items-center gap-0.5 border-b border-line px-2" role="tablist">
        {tabs.map(([k, label, badge]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => onTab(k)}
            className={cx('flex items-center gap-1 border-b-2 px-2 py-2.5 text-[12px]', tab === k ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg')}>
            {label}
            {badge ? <span className={cx('rounded px-1 text-[10px]', k === 'agents' ? 'pulse bg-accent font-bold text-bg' : 'bg-elev text-faint')}>{badge}</span> : null}
          </button>
        ))}
        <button type="button" onClick={onClose} className="ml-auto p-1 text-faint hover:text-fg" title={t.close}><X size={15} /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'agents' && <AgentsPanel runs={runs} flow={flow} focusRunId={focusRunId} onLoadRun={onLoadRun} />}
        {tab === 'plan' && <PlanBoard flow={flow} runs={runs} onOpenRun={onOpenRun} />}
        {tab === 'files' && <FilesPanel sessionId={sessionId} busy={busy} version={filesVersion} />}
        {tab === 'git' && <GitPanel sessionId={sessionId} busy={busy} version={filesVersion} onOpenSettings={onOpenSettings} />}
        {tab === 'trace' && <TracePanel sessionId={sessionId} live={liveJournal} />}
        {tab === 'mods' && <ModPanels ui={modUi} onManage={onManageMods} />}
      </div>
    </aside>
  );
}
