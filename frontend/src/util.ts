import type { Tier } from './types';

export const TIERS: Tier[] = ['fable', 'heavy', 'careful', 'medium', 'light'];

export const fmtTokens = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n));

export const fmtDuration = (ms: number): string => {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
};

export const fmtAgo = (ts: number, lang: string): string => {
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' });
  const s = (ts - Date.now()) / 1000;
  if (Math.abs(s) < 60) return rtf.format(Math.round(s), 'second');
  if (Math.abs(s) < 3600) return rtf.format(Math.round(s / 60), 'minute');
  if (Math.abs(s) < 86400) return rtf.format(Math.round(s / 3600), 'hour');
  return rtf.format(Math.round(s / 86400), 'day');
};

export const agentColor = (agent: string): string =>
  agent === 'plan' ? 'var(--plan)' : agent === 'flow' || agent.startsWith('savvy') ? 'var(--flow)' : 'var(--build)';

export const cx = (...xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(' ');
