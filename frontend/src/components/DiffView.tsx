export function DiffView({ diff }: { diff: string }) {
  const lines = diff.replace(/\n$/, '').split('\n');
  return (
    <pre className="max-h-96 overflow-auto rounded border border-line bg-panel py-1 text-[12px] leading-[1.45]">
      {lines.map((l, i) => {
        const cls =
          l.startsWith('+++') || l.startsWith('---')
            ? 'text-faint'
            : l.startsWith('@@')
              ? 'text-accent/80'
              : l.startsWith('+')
                ? 'bg-ok/10 text-ok'
                : l.startsWith('-')
                  ? 'bg-err/10 text-err'
                  : 'text-muted';
        return (
          <div key={i} className={`px-3 ${cls}`}>
            {l || ' '}
          </div>
        );
      })}
    </pre>
  );
}
