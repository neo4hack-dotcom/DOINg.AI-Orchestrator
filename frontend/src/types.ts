/* Mirrors the JSON the Python backend sends (backend/session.py, flow.py). */

export type AgentMode = 'build' | 'plan' | 'flow';
export type Tier = 'fable' | 'heavy' | 'careful' | 'medium' | 'light';
export type Phase = 'plan' | 'design' | 'delegate' | 'review' | 'verify' | 'close';

export interface TextPart { id: string; type: 'text' | 'reasoning' | 'notice' | 'error'; text: string; hidden?: boolean; goal?: boolean; command?: string; startedAt?: number; endedAt?: number }

export interface CompactionPart { id: string; type: 'compaction'; text: string; before: number; after: number; hidden?: boolean }

export interface ToolPart {
  id: string;
  type: 'tool';
  tool: string;
  callId: string;
  input: Record<string, any>;
  status: 'pending' | 'running' | 'waiting' | 'done' | 'error';
  title: string;
  output: string;
  metadata: Record<string, any>;
  permissionId?: string;
  startedAt?: number;
  endedAt?: number;
}

export interface PlannedTaskView {
  n: number;
  title: string;
  tier: Tier;
  after: number[];
  brief: string;
  verify: string;
  verifyCommand?: string;
  size?: 'S' | 'M' | 'L';
  complexity?: number;
  files?: string[];
  module?: string;
  wave?: number;
  priority?: number;
  merged?: number;
}

export interface PlanStats { waves: number; maxWidth: number; criticalPath: number; parallelism: number; conflictEdges: number; merged: number }

export interface ModuleView { id: string; name: string; goal: string; dependsOn: string[]; files: string[]; contracts?: string; verifyCommand?: string; gate?: number }

export interface Check { command: string; exitCode: number }

export interface PlanPart {
  id: string;
  type: 'plan';
  flowId: string;
  title: string;
  summary: string;
  design: string;
  spec: string;
  questions: string[];
  tasks: PlannedTaskView[];
  mode?: 'simple' | 'modular';
  modules?: ModuleView[];
  stats?: PlanStats;
  verifyCommands?: string[];
}

export interface ReviewPart {
  id: string;
  type: 'review';
  taskN: number;
  title: string;
  tier: Tier;
  round: number;
  runId: string;
  status: 'reviewing' | 'done';
  verdict: 'accept' | 'return' | null;
  defects: string[];
  note: string;
  files: string[];
  auto?: boolean;
  checks?: Check[];
}

export interface ShellPart {
  id: string;
  type: 'shell';
  command: string;
  output: string;
  exitCode: number | null;
  status: 'running' | 'done' | 'error';
  procId?: string;
  hidden?: boolean;
}

export type Part = TextPart | CompactionPart | ToolPart | PlanPart | ReviewPart | ShellPart;

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  agent: string;
  model: string;
  createdAt: number;
  parts: Part[];
  tokens: { input: number; output: number };
  reverted?: boolean;
  snapshot?: string;
}

export interface FlowTask extends PlannedTaskView {
  status: 'planned' | 'running' | 'review' | 'accepted' | 'failed' | 'blocked';
  round: number;
  runIds: string[];
  defects: string[];
  note: string;
  checks?: Check[];
  fix?: boolean;
  escalated?: boolean;
  startedAt?: number;
  endedAt?: number;
}

export interface FlowNote { author: string; task: number | null; text: string; ts: number }

export interface Flow {
  id: string;
  request: string;
  title: string;
  summary: string;
  total: number;
  done: number;
  running: number;
  phase: Phase;
  isFinished: boolean;
  tasks: FlowTask[];
  design: string;
  touchesDesign: boolean;
  greenfield: boolean;
  questions: string[];
  awaiting: 'approval' | 'answers' | null;
  interrupted: boolean;
  failed?: boolean;
  startedAt: number;
  endedAt: number | null;
  mode?: 'simple' | 'modular';
  modules?: ModuleView[];
  stats?: PlanStats;
  notes?: FlowNote[];
  verifyCommands?: string[];
  integration?: Check[];
  tokens?: number;
  budgetExceeded?: boolean;
  executing?: boolean;
  resumeCount?: number;
}

export interface AgentRun {
  id: string;
  type: string;
  tier: Tier;
  description: string;
  model: string;
  effort?: string;
  status: 'running' | 'done' | 'failed';
  startedAt: number;
  endedAt: number | null;
  contextTokens: number;
  contextMax: number;
  tokens: number;
  costUsd: number;
  steps: number;
  round: number;
  stepDone: number | null;
  stepTotal: number | null;
  stepNote: string;
  taskN: number | null;
  flowId: string | null;
  report: string;
  verify?: Check[];
  messages?: Message[];
}

export interface Todo { id: string; content: string; status: 'pending' | 'in_progress' | 'completed' | 'cancelled' }

export interface Permission {
  id: string;
  category: string;
  tool: string;
  input: Record<string, any>;
  agent: string;
  runId: string | null;
  messageId: string;
  partId: string;
  status: 'pending' | 'allowed' | 'rejected';
  note?: string;
}

export interface QuestionPrompt { question: string; options: string[]; multiple: boolean }

export interface Question {
  id: string;
  agent: string;
  runId: string | null;
  questions: QuestionPrompt[];
  status: 'pending' | 'answered' | 'rejected';
}

export interface Goal { condition: string; status: 'active' | 'achieved' | 'paused'; continuations: number; reason: string }

export interface JournalEntry { ts: number; kind: string; [key: string]: any }

export interface ChangedFile { path: string; additions: number; deletions: number; status: 'added' | 'modified' | 'deleted' }

export interface SessionSummary {
  id: string;
  title: string;
  agent: AgentMode;
  directory: string;
  createdAt: number;
  updatedAt: number;
  busy: boolean;
  flowTitle?: string | null;
  goalActive?: boolean;
  autoMode?: boolean | null;
  permissionMode?: PermissionMode | null;
}

export type PermissionMode = 'ask' | 'acceptEdits' | 'auto';

export interface Session extends SessionSummary {
  messages: Message[];
  todos: Todo[];
  runs: AgentRun[];
  flow: Flow | null;
  pendingPermissions: Permission[];
  pendingQuestions: Question[];
  tokens: { input: number; output: number };
  contextTokens?: number;
  compactions?: number;
  goal?: Goal | null;
  pullRequests?: PullRequest[];
  baseSnapshot?: string | null;
  seq?: number;
  modUi?: ModUi;
  mcpDisabled?: string[];
  skillsUsed?: string[];
  mcpCalls?: Record<string, number>;
  datasets?: DatasetMeta[];
}

/* ---- terminal, files, preview ---- */

export interface ProcInfo {
  id: string;
  command: string;
  cwd: string;
  owner: 'user' | 'agent';
  kind: 'terminal' | 'background' | 'shell';
  runId: string | null;
  startedAt: number;
  endedAt: number | null;
  exitCode: number | null;
  status: 'running' | 'exited' | 'killed';
  urls: string[];
  size: number;
  output?: string;
}

export interface TreeEntry { name: string; path: string; dir: boolean; size: number; mtime: number; ignored: boolean; symlink: boolean }

export interface FileData { path: string; size: number; mtime: number; mime: string; binary: boolean; content: string | null; tooLarge: boolean; crlf: boolean }

export interface UiShow { kind: 'file' | 'url'; path?: string; line?: number | null; preview?: boolean; url?: string; agent?: string }

/* ---- data analysis ---- */

export interface DataSource { id: string; kind: 'mcp' | 'sqlite' | 'file'; name: string; dialect?: string; queryTool?: string | null;
  writeTool?: string | null; listTool?: string | null; table?: string; error?: string }

export type FilterOp = 'eq' | 'neq' | 'contains' | 'not_contains' | 'starts' | 'ends' | 'gt' | 'gte' | 'lt' | 'lte' | 'empty' | 'not_empty' | 'in' | 'regex';

export interface DataView {
  filters: { column: string; op: FilterOp; value?: any }[];
  sort: { column: string; desc: boolean }[];
  hidden: string[];
  search: string;
}

export interface DatasetMeta {
  id: string; title: string; source: string; sql: string; columns: string[]; types: string[]; rowCount: number; truncated: boolean;
  createdAt: number; elapsedMs: number; createdBy: 'user' | 'agent'; view: DataView; affected?: number;
}

export interface DataPage { dataset: DatasetMeta; columns: string[]; types: string[]; rows: any[][]; total: number; filtered: number; offset: number; limit: number }

export interface ColumnProfile { column: string; type: string; count: number; nulls: number; distinct: number; top: [string, number][];
  min?: any; max?: any; mean?: number; median?: number; std?: number; sum?: number; minLength?: number; maxLength?: number }

export interface DataProfile { rows: number; duplicates: number; columns: ColumnProfile[] }

export interface PrivacyEndpoint { kind: string; name: string; url: string; local: boolean; blocked: boolean; note: string }

export interface PrivacyReport { localOnly: boolean; allowPackageManagers: boolean; endpoints: PrivacyEndpoint[]; token: boolean; host: string;
  checks: { name: string; ok: boolean; detail: string }[] }

/* ---- extensions: mods, MCP, skills, reverse engineering ---- */

export interface ModBand { markdown: string; actions: { label: string; command: string }[] }
export interface ModUi { status: Record<string, string>; band: Record<string, ModBand>; panels: Record<string, { title: string; markdown: string }> }
export interface ModToast { id: number; mod: string; text: string; kind: 'info' | 'success' | 'warning' | 'error' }

export interface ModInfo {
  name: string;
  version: string;
  description: string;
  scope: 'builtin' | 'user';
  path: string;
  enabled: boolean;
  defaultEnabled: boolean;
  error: string;
  errors: number;
  events: string[];
  commands: { name: string; description: string; argumentHint: string }[];
  tools: { name: string; description: string }[];
  options: Record<string, any>;
  optionDocs: Record<string, string>;
  logs: { t: number; level: string; text: string }[];
  readOnly: boolean;
  inspiredBy?: string;
}

export interface CommandInfo { name: string; description: string; argumentHint: string; source: string }

export interface McpServer {
  name: string;
  type: 'stdio' | 'http' | 'sse';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  cwd?: string;
  url?: string;
  headers?: Record<string, string>;
  enabled: boolean;
  permission: Perm;
  trustReadOnly: boolean;
  timeout?: number;
  disabledTools?: string[];
  description?: string;
  source?: 'user' | 'reverse' | 'project';
  approved?: boolean;
  live?: { status: string; error: string; tools: number; server: { name?: string; version?: string } } | null;
}

export interface McpPreset { name: string; title: string; type?: string; command?: string; args?: string[]; url?: string; headers?: Record<string, string>; description: string }

export interface McpOverview {
  servers: McpServer[];
  project: McpServer[];
  directory: string;
  presets: McpPreset[];
  settings: { deferThreshold: number; maxDescriptionChars: number; inPlanMode: boolean; inWorkers: boolean };
}

export interface McpTestResult {
  ok: boolean;
  status: string;
  error: string;
  server: { name?: string; version?: string };
  tools: { name: string; description: string; readOnly: boolean }[];
  resources: boolean;
  log: string[];
}

export interface SkillSummary {
  name: string;
  description: string;
  scope: 'builtin' | 'user' | 'project';
  path: string;
  whenToUse: string;
  argumentHint: string;
  allowedTools: string[];
  modelInvocable: boolean;
  userInvocable: boolean;
  requires: string;
  license: string;
  enabled: boolean;
  available: boolean;
  error: string;
  readOnly: boolean;
}

export interface SkillDetail extends SkillSummary { content: string; files: string[] }

export interface ReverseStatus {
  config: AppConfig['reverse'];
  node: { version: string; ok: boolean };
  npx: boolean;
  server: McpServer;
  live: McpServer['live'];
  disclaimer: string;
}

/* ---- activity / report ---- */

export interface ActivityItem {
  ts: number;
  kind: 'request' | 'reasoning' | 'answer' | 'tool' | 'review' | 'notice' | 'error' | 'compaction' | 'plan' | 'event';
  agent?: string;
  runId?: string | null;
  runTitle?: string | null;
  text?: string;
  [key: string]: any;
}

export interface Activity {
  generatedAt: number;
  overview: {
    id: string; title: string; directory: string; createdAt: number; updatedAt: number; durationMs: number; agent: string;
    autoMode: boolean | null; model: string; provider: string; orchestratorModel: string; tokensIn: number; tokensOut: number;
    compactions: number; requests: number; reasoningSteps: number; toolCalls: number; toolErrors: number; filesChanged: number;
    checks: number; checksPassed: number; workers: number; reviews: number; lastAnswer: string;
  };
  requests: ActivityItem[];
  reasoning: ActivityItem[];
  answers: ActivityItem[];
  plan: null | {
    title: string; summary: string; request: string; mode: string; phase: string; done: number; total: number; isFinished: boolean;
    failed?: boolean; tokens: number; modules: ModuleView[];
    tasks: { n: number; title: string; module: string; tier: Tier; size: string; complexity: number; status: string; rounds: number;
      files: string[]; verifyCommand: string; checks: Check[]; note: string; defects: string[]; durationMs: number; tokens: number }[];
    verifyCommands: string[]; integration: Check[]; spec: string; design: string;
  };
  workers: { id: string; type: string; tier: Tier; description: string; model: string; status: string; startedAt: number;
    endedAt: number | null; steps: number; tokens: number; round: number; report: string }[];
  validation: { ts: number; source: string; command: string; exitCode: number | null; durationMs: number | null; ok: boolean; kind: string }[];
  reviews: ActivityItem[];
  tools: ActivityItem[];
  toolStats: { tool: string; calls: number; errors: number; ms: number }[];
  files: { path: string; additions: number; deletions: number; edits: number; agents: string[] }[];
  permissions: { createdAt: number; agent: string; tool: string; category: string; status: string; target: string }[];
  questions: { createdAt: number; agent: string; questions: string[]; answers: string[][] | null; status: string }[];
  extensions: { mcp: { server: string; calls: number }[]; skills: string[]; mods: JournalEntry[]; blocked: ActivityItem[] };
  issues: ActivityItem[];
  pullRequests: PullRequest[];
  timeline: ActivityItem[];
}

export interface LlmConfig {
  provider: 'ollama' | 'openai_compatible' | 'n8n';
  baseUrl: string;
  apiKey: string;
  model: string;
  systemPrompt: string;
  toolMode: 'auto' | 'native' | 'prompt';
  contextWindow: number;
  temperature: number;
  timeout: number;
  responseLanguage: string;
}

export type Perm = 'allow' | 'ask' | 'deny';

export interface AppConfig {
  llm: LlmConfig;
  tiers: Record<Tier, { model: string; effort: 'low' | 'medium' | 'high' | 'xhigh' }>;
  orchestratorModel: string;
  workspace: string;
  permissionMode: PermissionMode;
  network: { localOnly: boolean; allowPackageManagers: boolean };
  data: { enabled: boolean; maxRows: number; previewRows: number };
  terminal: { maxProcesses: number; shellTimeout: number };
  permissions: {
    edit: Perm;
    bash: Perm;
    webfetch: Perm;
    mcp: Perm;
    mods: Perm;
    open: Perm;
    data: Perm;
    bashRules: { pattern: string; action: Perm }[];
  };
  mcp: { deferThreshold: number; maxDescriptionChars: number; inPlanMode: boolean; inWorkers: boolean };
  skills: { disabled: string[]; modelInvocation: boolean };
  mods: { disabled: string[]; enabled: string[]; options: Record<string, Record<string, any>> };
  reverse: { enabled: boolean; acknowledged: boolean; version: string; provider: 'auto' | 'hopper' | 'ghidra' | 'ida'; schemaProfile: 'compact' | 'full'; timeout: number; env: Record<string, string> };
  flow: {
    requirePlanApproval: boolean;
    maxParallel: number;
    maxReturnRounds: number;
    gitCommit: boolean;
    planSteps: number;
    reviewSteps: number;
    hierarchical: boolean;
    splitLargeTasks: boolean;
    mergeSmallTasks: boolean;
    autoVerify: boolean;
    autoFixRetries: number;
    fastAcceptLight: boolean;
    escalateOnFail: boolean;
    integrationRounds: number;
    taskTimeoutMinutes: number;
    tokenBudget: number;
    autoResume: boolean;
    writeReport: boolean;
  };
  verify: { commands: string[]; timeout: number };
  context: { toolOutputMaxChars: number; pruneAt: number; compactAt: number; keepRecentShare: number; repoMap: boolean; repoMapChars: number };
  agent: { maxRetries: number; retryBaseSeconds: number; doomLoopThreshold: number; requireReadBeforeEdit: boolean; goalMaxContinuations: number };
  nexusDbPath: string;
  projectsRoot: string;
  recentProjects: string[];
  shell: string;
  autoMode: boolean;
  bitbucket: BitbucketConfig;
}

export interface BitbucketConfig {
  kind: 'server' | 'cloud';
  baseUrl: string;
  project: string;
  repo: string;
  username: string;
  token: string;
  targetBranch: string;
  branchPrefix: string;
  reviewers: string[];
  remote: string;
  autoPrOnFlow: boolean;
  verifySsl: boolean;
}

export interface PullRequest { id: number; url: string; branch: string; target: string; steps: string[]; title: string; createdAt: number; existing?: boolean }

export interface GitStatus {
  isRepo: boolean;
  branch?: string;
  hasCommits?: boolean;
  remoteUrl?: string;
  remote?: { kind: string; project: string; repo: string; baseUrl: string } | null;
  changes?: string[];
  branches?: string[];
  draft?: { title: string; description: string };
  configured?: boolean;
  target?: { kind?: string; project?: string; repo?: string; error?: string } | null;
  pullRequests?: PullRequest[];
}

export interface FolderListing {
  path: string;
  parent: string | null;
  dirs: { name: string; path: string; isProject: boolean }[];
  roots: string[];
  home: string;
  projectsRoot: string;
  recent: string[];
  sep: string;
}

export type ServerEvent = { seq?: number } & (
  | { type: 'hello'; busy: boolean }
  | { type: 'message.created'; runId: string | null; message: Message }
  | { type: 'message.updated'; runId: string | null; message: Omit<Message, 'parts'> }
  | { type: 'part.updated'; runId: string | null; messageId: string; part: Part }
  | { type: 'part.delta'; runId: string | null; messageId: string; partId: string; delta: string }
  | { type: 'run.updated'; run: Omit<AgentRun, 'messages'> }
  | { type: 'flow.updated'; flow: Flow }
  | { type: 'todos.updated'; todos: Todo[] }
  | { type: 'permission.asked'; permission: Permission }
  | { type: 'permission.resolved'; permission: Permission }
  | { type: 'status'; busy: boolean }
  | { type: 'session.updated'; session: SessionSummary }
  | { type: 'session.reload' }
  | { type: 'question.asked'; question: Question }
  | { type: 'question.resolved'; question: Question }
  | { type: 'goal.updated'; goal: Goal | null }
  | { type: 'journal'; entry: JournalEntry }
  | { type: 'pr.created'; pr: PullRequest }
  | { type: 'mods.ui'; modUi: ModUi }
  | { type: 'mods.toast'; mod: string; text: string; kind: ModToast['kind'] }
  | { type: 'mods.changed'; mods: ModInfo[] }
  | { type: 'skills.changed' }
  | { type: 'terminal.updated'; process: ProcInfo }
  | { type: 'terminal.output'; procId: string; offset: number; chunk: string }
  | { type: 'terminal.removed'; procId: string }
  | { type: 'terminal.cwd'; cwd: string }
  | { type: 'ui.show' } & UiShow
  | { type: 'files.changed'; paths: string[] }
  | { type: 'data.dataset'; dataset: DatasetMeta }
  | { type: 'data.view'; datasetId: string; view: DataView; by: string });
