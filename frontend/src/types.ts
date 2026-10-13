/* Mirrors the JSON the Python backend sends (backend/session.py, flow.py). */

export type AgentMode = 'build' | 'plan' | 'flow';
export type Tier = 'fable' | 'heavy' | 'careful' | 'medium' | 'light';
export type Phase = 'plan' | 'design' | 'delegate' | 'review' | 'verify' | 'close';

export interface TextPart { id: string; type: 'text' | 'reasoning' | 'notice' | 'error'; text: string; hidden?: boolean; goal?: boolean }

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

export type Part = TextPart | CompactionPart | ToolPart | PlanPart | ReviewPart;

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
}

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
  permissions: {
    edit: Perm;
    bash: Perm;
    webfetch: Perm;
    bashRules: { pattern: string; action: Perm }[];
  };
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
  | { type: 'pr.created'; pr: PullRequest });
