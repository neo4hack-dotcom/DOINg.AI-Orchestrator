import { createContext, useContext, useState } from 'react';
import { Check, ChevronRight, Database, FileSearch, Loader2, ShieldQuestion, X } from 'lucide-react';
import type { ToolPart } from '../types';
import { DiffView } from './DiffView';
import { TierChip } from './TierIcon';

/* What tool cards can open in the app: a file in the editor, a dataset in the Data view. */
export const ToolActions = createContext<{ openFile?: (path: string, line?: number | null) => void; openDataset?: (id: string) => void }>({});

const FILE_TOOLS = new Set(['read', 'write', 'edit', 'multiedit', 'show']);

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
      return { verb: '$', target: i.command || '', extra: i.run_in_background ? 'background' : m.exitCode !== undefined && m.exitCode !== 0 ? `exit ${m.exitCode}` : undefined };
    case 'bash_output':
      return { verb: 'Output', target: i.id || '', extra: m.status };
    case 'bash_kill':
      return { verb: 'Stop', target: i.id || '' };
    case 'show':
      return { verb: 'Show', target: i.path || i.url || '', extra: i.line ? `line ${i.line}` : undefined };
    case 'open':
      return { verb: 'Open', target: i.target || i.path || i.url || '' };
    case 'webfetch':
      return { verb: 'Fetch', target: i.url || '' };
    case 'data_sources':
      return { verb: 'Data sources', target: '' };
    case 'data_query':
      return { verb: `SQL ${i.source || ''}`, target: String(i.sql || '').replace(/\s+/g, ' ').slice(0, 120), extra: p.title };
    case 'data_view':
      return { verb: 'Data view', target: i.dataset || '', extra: p.title };
    case 'data_profile':
      return { verb: 'Profile', target: i.dataset || '' };
    case 'data_export':
      return { verb: 'Export', target: i.dataset || '', extra: i.format };
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

/* The first rows of a query result, right in the transcript. */
function DataPreview({ data }: { data: any }) {
  const { openDataset } = useContext(ToolActions);
  const ds = data?.dataset;
  const preview = data?.preview;
  if (!ds) return null;
  return (
    <div className="ml-6 mt-1 overflow-hidden rounded border border-line">
      {preview?.columns?.length > 0 && (
        <div className="max-h-56 overflow-auto">
          <table className="w-full border-collapse font-mono text-[11.5px]">
            <thead className="sticky top-0 bg-elev">
              <tr>{preview.columns.map((c: string) => <th key={c} className="whitespace-nowrap border-b border-line px-2 py-0.5 text-left font-semibold text-muted">{c}</th>)}</tr>
            </thead>
            <tbody>
              {preview.rows.map((r: any[], k: number) => (
                <tr key={k} className="odd:bg-panel/60">
                  {r.map((v, j) => <td key={j} className="max-w-[220px] truncate whitespace-nowrap px-2 py-0.5">{v === null || v === undefined ? <span className="text-faint">null</span> : String(v)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex items-center gap-2 border-t border-line bg-panel px-2 py-1 text-[11px] text-faint">
        <span className="min-w-0 truncate">{ds.title} · {ds.rowCount.toLocaleString()} rows{ds.truncated ? ' (truncated)' : ''} · {ds.columns.length} columns</span>
        {openDataset && (
          <button type="button" onClick={() => openDataset(ds.id)} className="ml-auto inline-flex shrink-0 items-center gap-1 rounded border border-accent/50 px-1.5 text-accent hover:bg-accent/10">
            <Database size={11} /> Open in the Data view
          </button>
        )}
      </div>
    </div>
  );
}

export function ToolPartView({ part }: { part: ToolPart }) {
  const [open, setOpen] = useState(false);
  const { openFile } = useContext(ToolActions);
  const { verb, target, extra } = label(part);
  const diff: string | undefined = part.metadata?.diff;
  const hasBody = Boolean(diff || part.output || part.tool === 'task');
  const data = part.status === 'done' && part.tool !== 'data_sources' ? part.metadata?.data : null;
  const path: string | undefined = FILE_TOOLS.has(part.tool) && part.input?.path ? part.input.path : undefined;
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
      <div className="group flex min-w-0 items-center rounded hover:bg-elev">
      <button
        type="button"
        onClick={() => hasBody && setOpen((o) => !o)}
        className="flex min-w-0 flex-1 items-center gap-2 px-1 py-0.5 text-left"
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
      {path && openFile && part.status !== 'error' && (
        <button type="button" title="Open in the editor" onClick={() => openFile(path, part.input?.line ?? part.input?.offset ?? null)}
          className="mr-1 shrink-0 rounded p-0.5 text-faint opacity-0 hover:text-fg focus:opacity-100 group-hover:opacity-100">
          <FileSearch size={12} />
        </button>
      )}
      </div>
      {data?.preview && <DataPreview data={data} />}
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
