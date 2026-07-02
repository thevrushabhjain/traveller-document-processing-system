'use client';

import { cn } from '@/lib/cn';

const tone = {
  high: 'bg-white text-confHigh border-confHigh',
  mid: 'bg-white text-confMid border-confMid',
  low: 'bg-white text-confLow border-confLow',
};

export function ConfidenceBadge({ value, dotOnly = false, testId }) {
  const n = typeof value === 'number' ? value : 0;
  const pct = Math.round(n * 100);
  const level = n >= 0.85 ? 'high' : n >= 0.6 ? 'mid' : 'low';
  const dotColor = level === 'high' ? 'bg-confHigh' : level === 'mid' ? 'bg-confMid' : 'bg-confLow';

  if (dotOnly) {
    return (
      <span className="inline-flex items-center gap-2" data-testid={testId}>
        <span className={cn('h-2 w-2 rounded-full', dotColor)} aria-hidden />
        <span className="font-mono text-[11px] tracking-wider">{pct}%</span>
      </span>
    );
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-2 border px-2 py-0.5 text-[11px] font-mono uppercase tracking-widest',
        tone[level],
      )}
      data-testid={testId}
    >
      <span className={cn('h-1.5 w-1.5', dotColor)} aria-hidden />
      {pct}% conf
    </span>
  );
}
