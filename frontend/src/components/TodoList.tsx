import { useState } from 'react';
import { CheckSquare, ChevronDown, ChevronRight, Square, SquareDot, SquareX } from 'lucide-react';
import type { Todo } from '../types';
import { useT } from '../i18n';
import { cx } from '../util';

export function TodoList({ todos }: { todos: Todo[] }) {
  const t = useT();
  const [open, setOpen] = useState(true);
  const openCount = todos.filter((x) => x.status === 'pending' || x.status === 'in_progress').length;
  if (!todos.length || !openCount) return null;
  const done = todos.length - openCount;
  return (
    <div className="rounded-md border border-line bg-panel px-3 py-1.5">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-1.5 text-[12px] text-muted">
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="font-semibold">{t.todos}</span>
        <span className="text-faint">{done}/{todos.length}</span>
      </button>
      {open && (
        <ul className="mt-1 max-h-36 space-y-0.5 overflow-y-auto pl-4 text-[12px]">
          {todos.map((x) => (
            <li key={x.id} className={cx('flex items-start gap-1.5', x.status === 'completed' || x.status === 'cancelled' ? 'text-faint line-through' : x.status === 'in_progress' ? 'text-accent' : 'text-fg')}>
              <span className="mt-[3px] shrink-0">
                {x.status === 'completed' ? <CheckSquare size={12} /> : x.status === 'in_progress' ? <SquareDot size={12} /> : x.status === 'cancelled' ? <SquareX size={12} /> : <Square size={12} />}
              </span>
              {x.content}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
