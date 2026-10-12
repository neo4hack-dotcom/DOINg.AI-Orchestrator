import { useEffect, useState } from 'react';
import { MessageCircleQuestion } from 'lucide-react';
import type { Question } from '../types';
import { useT } from '../i18n';
import { cx } from '../util';

/* An agent's question (question tool): choices as toggles, or free text. */
export function QuestionBar({ question, onAnswer }: { question: Question | undefined; onAnswer: (id: string, answers: string[][]) => void }) {
  const t = useT();
  const [picked, setPicked] = useState<string[][]>([]);
  const [free, setFree] = useState<string[]>([]);
  useEffect(() => {
    setPicked(question ? question.questions.map(() => []) : []);
    setFree(question ? question.questions.map(() => '') : []);
  }, [question?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!question) return null;

  const toggle = (qi: number, opt: string, multiple: boolean) =>
    setPicked((p) => p.map((sel, i) => (i !== qi ? sel : multiple ? (sel.includes(opt) ? sel.filter((x) => x !== opt) : [...sel, opt]) : [opt])));
  const answers = question.questions.map((_, i) => [...(picked[i] || []), ...(free[i]?.trim() ? [free[i].trim()] : [])]);
  const ready = answers.every((a) => a.length > 0);

  return (
    <div className="rounded-md border border-accent/50 bg-accent/5 px-3 py-2" role="dialog" aria-label={t.questionTitle}>
      <div className="mb-1.5 flex items-center gap-2 font-semibold text-accent">
        <MessageCircleQuestion size={14} /> {t.questionTitle}
        <span className="text-[11px] font-normal text-faint">{question.agent}</span>
      </div>
      <div className="space-y-2.5">
        {question.questions.map((q, qi) => (
          <div key={qi}>
            <div className="mb-1 text-fg">{q.question}</div>
            {q.options.length > 0 && (
              <div className="mb-1 flex flex-wrap gap-1.5">
                {q.options.map((opt) => (
                  <button key={opt} type="button" onClick={() => toggle(qi, opt, q.multiple)}
                    className={cx('rounded border px-2 py-0.5 text-[12px]', picked[qi]?.includes(opt) ? 'border-accent bg-accent/15 text-accent' : 'border-line text-muted hover:text-fg')}>
                    {opt}
                  </button>
                ))}
              </div>
            )}
            <input
              value={free[qi] || ''}
              onChange={(e) => setFree((f) => f.map((v, i) => (i === qi ? e.target.value : v)))}
              onKeyDown={(e) => e.key === 'Enter' && ready && onAnswer(question.id, answers)}
              placeholder={t.yourAnswer}
              className="w-full rounded border border-line bg-bg px-2 py-1 text-[12px] outline-none focus:border-accent"
            />
          </div>
        ))}
      </div>
      <button type="button" disabled={!ready} onClick={() => onAnswer(question.id, answers)}
        className="mt-2 rounded bg-accent px-3 py-1 text-[12px] font-semibold text-bg disabled:opacity-40">
        {t.answer}
      </button>
    </div>
  );
}
