import type { Tier } from '../types';

/* The savvy-progress crew: astronaut floats, detective sweeps the magnifier, engineer
   turns the wrench, chef tosses the omelette, racer runs. Unknown workers are crabs. */
const ICONS: Record<string, string> = {
  fable: '🧑‍🚀',
  heavy: '🕵️',
  careful: '🧑‍🔧',
  medium: '🧑‍🍳',
  light: '🏎️',
};

export function TierIcon({ tier, active = false, size = 18 }: { tier?: Tier | string; active?: boolean; size?: number }) {
  const known = tier && ICONS[tier];
  const anim = active ? (known ? `tier-anim-${tier}` : 'tier-anim-crab') : '';
  return (
    <span
      aria-hidden
      className={`inline-block select-none leading-none ${anim}`}
      style={{ fontSize: size, width: size + 4, textAlign: 'center' }}
    >
      {known || '🦀'}
    </span>
  );
}

export const TIER_COLOR: Record<string, string> = {
  fable: '#c792ea',
  heavy: '#e0707a',
  careful: '#e5b567',
  medium: '#5c9cf5',
  light: '#5fbf8f',
};

export function TierChip({ tier }: { tier: string }) {
  const c = TIER_COLOR[tier] || 'var(--muted)';
  return (
    <span
      className="inline-flex items-center rounded px-1.5 text-[11px] font-semibold uppercase tracking-wide"
      style={{ color: c, background: `color-mix(in srgb, ${c} 14%, transparent)` }}
    >
      {tier}
    </span>
  );
}
