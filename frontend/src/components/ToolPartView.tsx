import { useState } from 'react';
import { Check, ChevronRight, Loader2, ShieldQuestion, X } from 'lucide-react';
import type { ToolPart } from '../types';
import { DiffView } from './DiffView';
import { TierChip } from './TierIcon';

function label(p: ToolPart): { verb: string; target: string; extra?: string } {
  const i = p.input || {};
  const m = p.metadata || {};
  switch (p.tool) {
    case 'read':
      return { verb: 'Read', target: i.path || '', extra: m.lines ? `${m.lines} lines` : undefined };
    case 'list':
      return { verb: 'List', target: i.path || '.' };
    case 'glob':
      return { verb: 'Glob', target: i.pattern || '', extra: m.count !== undefined ? `${m.count}` : undefined };
    case 'grep':
      return { verb: 'Grep', target: i.pattern || '', extra: m.count !== undefined ? `${m.count}` : undefined };
    case 'write':
      return { verb: 'Write', target: i.path || '', extra: m.additions !== undefined ? `+${m.additions}` : undefined };
    case 'edit':
      return { verb: 'Edit', target: i.path || '', extra: m.additions !== undefined ? `+${m.additions} -${m.deletions}` : undefined };
    case 'bash':
      return { verb: '$', target: i.command || '', extra: m.exitCode !== undefined && m.exitCode !== 0 ? `exit ${m.exitCode}` : undefined };
    case 'todowrite':
      return { verb: 'Todos', target: `${(i.todos || []).length} items` };
    case 'todoread':
      return { verb: 'Todos', target: 'read' };
    case 'step':
      return { verb: 'Step', target: `${i.done ?? 0}/${i.total ?? '?'}`, extra: i.note };
    case 'task':
      return { verb: 'Task', target: i.description || '' };
    case 'skill':
      return { verb: 'Skill', target: i.name || '', extra: i.arguments ? String(i.arguments).slice(0, 40) : undefined };
    case 'mcp_search':
      return { verb: 'MCP search', target: i.query || '' };
    case 'mcp_call':
      return { verb: `MCP ${i.server || ''}`, target: i.tool || '', extra: JSON.stringify(i.arguments || {}).slice(0, 60) };
    case 'mcp_resources':
      return { verb: `MCP ${i.server || ''}`, target: i.uri || 'resources' };
    default:
      if (p.tool.startsWith('mcp__')) {
        const [, server, tool] = p.tool.split('__');
        return { verb: `MCP ${server}`, target: tool, extra: JSON.stringify(i).slice(0, 70) };
      }
      return { verb: p.tool, target: JSON.stringify(i).slice(0, 80) };
  }
}

export function ToolPartView({ part }: { part: ToolPart }) {
  const [open, setOpen] = useState(false);
  const { verb, target, extra } = label(part);
  const diff: string | undefined = part.metadata?.diff;
  const hasBody = Boolean(diff || part.output || part.tool === 'task');
  const statusIcon =
    part.status === 'running' || part.status === 'pending' ? (
      <Loader2 size={13} className="spin text-muted" />
    ) : part.status === 'waiting' ? (
      <ShieldQuestion size={13} className="pulse text-warn" />
    ) : part.status === 'error' ? (
      <X size={13} className="text-err" />
    ) : (
      <Check size={13} className="text-ok" />
    );

  return (
    <div className="my-1">
      <button
        type="button"
        onClick={() => hasBody && setOpen((o) => !o)}
        className="group flex w-full min-w-0 items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-elev"
        aria-expanded={open}
      >
        <ChevronRight size={12} className={`shrink-0 text-faint transition-transform ${open ? 'rotate-90' : ''} ${hasBody ? '' : 'invisible'}`} />
        {statusIcon}
        <span className={`shrink-0 font-semibold ${part.status === 'error' ? 'text-err' : 'text-fg'}`}>{verb}</span>
        {part.tool === 'task' && part.input?.tier && <TierChip tier={part.input.tier} />}
        <span className="min-w-0 truncate text-muted">{target}</span>
        {extra && <span className="min-w-0 shrink truncate text-[11px] text-faint">{extra}</span>}
        {part.metadata?.diagnostics?.length > 0 && (
          <span className="shrink-0 rounded bg-warn/15 px-1 text-[10px] text-warn" title={part.metadata.diagnostics.join('\n')}>⚠ {part.metadata.diagnostics.length}</span>
        )}
        {part.metadata?.blockedBy && <span className="shrink-0 rounded bg-err/15 px-1 text-[10px] text-err">blocked · {part.metadata.blockedBy}</span>}
        {part.status === 'error' && !open && <span className="min-w-0 truncate text-[11px] text-err/80">{part.output}</span>}
      </button>
      {open && (
        <div className="ml-6 mt-1">
          {diff ? (
            <DiffView diff={diff} />
          ) : (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-line bg-panel px-3 py-2 text-[12px] text-muted">
              {part.tool === 'task' && part.input?.prompt ? `${part.input.prompt}\n\n────────\n${part.output}` : part.output}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
