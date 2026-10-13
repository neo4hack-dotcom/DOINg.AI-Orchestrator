import type { AgentRun, Message, Part, ServerEvent, Session } from './types';

/* Applies server events to the open session. Only the touched branch is cloned so
   streaming deltas stay cheap. */

type MsgList = Message[];

function withMessages(s: Session, runId: string | null, fn: (list: MsgList) => MsgList): Session {
  if (!runId) return { ...s, messages: fn(s.messages) };
  // Worker transcripts are loaded on demand: ignore events for runs not loaded yet.
  const runs = s.runs.map((r) => (r.id === runId && r.messages ? { ...r, messages: fn(r.messages) } : r));
  return { ...s, runs };
}

function withMessage(list: MsgList, id: string, fn: (m: Message) => Message): MsgList {
  const i = list.findIndex((m) => m.id === id);
  if (i < 0) return list;
  const next = list.slice();
  next[i] = fn(list[i]);
  return next;
}

function upsertPart(parts: Part[], part: Part): Part[] {
  const i = parts.findIndex((p) => p.id === part.id);
  if (i < 0) return [...parts, part];
  const next = parts.slice();
  next[i] = part;
  return next;
}

export function applyEvent(s: Session, e: ServerEvent): Session {
  switch (e.type) {
    case 'message.created':
      return withMessages(s, e.runId, (list) => (list.some((m) => m.id === e.message.id) ? list : [...list, e.message]));
    case 'message.updated':
      return withMessages(s, e.runId, (list) => withMessage(list, e.message.id, (m) => ({ ...m, ...e.message })));
    case 'part.updated':
      return withMessages(s, e.runId, (list) =>
        withMessage(list, e.messageId, (m) => ({ ...m, parts: upsertPart(m.parts, e.part) })),
      );
    case 'part.delta':
      return withMessages(s, e.runId, (list) =>
        withMessage(list, e.messageId, (m) => ({
          ...m,
          parts: m.parts.map((p) => (p.id === e.partId && 'text' in p ? { ...p, text: (p.text || '') + e.delta } : p)),
        })),
      );
    case 'run.updated': {
      const i = s.runs.findIndex((r) => r.id === e.run.id);
      if (i < 0) return { ...s, runs: [...s.runs, { ...e.run, messages: [] } as AgentRun] };  // new run: live from start
      const runs = s.runs.slice();
      runs[i] = { ...runs[i], ...e.run };
      return { ...s, runs };
    }
    case 'flow.updated':
      return { ...s, flow: e.flow };
    case 'todos.updated':
      return { ...s, todos: e.todos };
    case 'permission.asked':
      return { ...s, pendingPermissions: [...s.pendingPermissions.filter((p) => p.id !== e.permission.id), e.permission] };
    case 'permission.resolved':
      return { ...s, pendingPermissions: s.pendingPermissions.filter((p) => p.id !== e.permission.id) };
    case 'question.asked':
      return { ...s, pendingQuestions: [...(s.pendingQuestions || []).filter((q) => q.id !== e.question.id), e.question] };
    case 'question.resolved':
      return { ...s, pendingQuestions: (s.pendingQuestions || []).filter((q) => q.id !== e.question.id) };
    case 'pr.created':
      return { ...s, pullRequests: [...(s.pullRequests || []), e.pr] };
    case 'goal.updated':
      return { ...s, goal: e.goal };
    case 'status':
      return { ...s, busy: e.busy };
    case 'session.updated':
      return { ...s, title: e.session.title, agent: e.session.agent, busy: e.session.busy };
    case 'mods.ui':
      return { ...s, modUi: e.modUi };
    default:
      return s;
  }
}
