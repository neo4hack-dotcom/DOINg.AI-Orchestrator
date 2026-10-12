/* Mirrors the JSON the Python backend sends (backend/session.py, flow.py). */

export type AgentMode = 'build' | 'plan' | 'flow';
export type Tier = 'fable' | 'heavy' | 'careful' | 'medium' | 'light';
export type Phase = 'plan' | 'design' | 'delegate' | 'review' | 'close';

export interface TextPart { id: string; type: 'text' | 'reasoning' | 'notice' | 'error'; text: string; hidden?: boolean }

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

export interface PlannedTaskView { n: number; title: string; tier: Tier; after: number[]; brief: string; verify: string }

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
}

export type Part = TextPart | ToolPart | PlanPart | ReviewPart;

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  agent: string;
  model: string;
  createdAt: number;
  parts: Part[];
  tokens: { input: number; output: number };
}

export interface FlowTask extends PlannedTaskView {
  status: 'planned' | 'running' | 'review' | 'accepted' | 'failed' | 'blocked';
  round: number;
  runIds: string[];
  defects: string[];
  note: string;
}

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
  messages: Message[];
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

export interface SessionSummary {
  id: string;
  title: string;
  agent: AgentMode;
  directory: string;
  createdAt: number;
  updatedAt: number;
  busy: boolean;
  flowTitle?: string | null;
}

export interface Session extends SessionSummary {
  messages: Message[];
  todos: Todo[];
  runs: AgentRun[];
  flow: Flow | null;
  pendingPermissions: Permission[];
  tokens: { input: number; output: number };
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
}

export interface AppConfig {
  llm: LlmConfig;
  tiers: Record<Tier, { model: string; effort: 'low' | 'medium' | 'high' | 'xhigh' }>;
  orchestratorModel: string;
  workspace: string;
  permissions: { edit: 'allow' | 'ask' | 'deny'; bash: 'allow' | 'ask' | 'deny' };
  flow: { requirePlanApproval: boolean; maxParallel: number; maxReturnRounds: number; gitCommit: boolean; planSteps: number; reviewSteps: number };
  nexusDbPath: string;
  language: 'auto' | 'fr' | 'en';
}

export type ServerEvent =
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
  | { type: 'session.updated'; session: SessionSummary };
