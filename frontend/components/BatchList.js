// frontend/components/BatchList.js
'use client';

import { CheckCircle, CircleNotch, XCircle, Clock } from '@phosphor-icons/react';

const STATUS_ICON = {
  PENDING: <Clock size={14} />,
  PROCESSING: <CircleNotch size={14} className="animate-spin" />,
  COMPLETED: <CheckCircle size={14} weight="fill" className="text-confHigh" />,
  FAILED: <XCircle size={14} weight="fill" className="text-confLow" />,
};

export default function BatchList({ items, activeId, onSelect }) {
  if (!items || items.length === 0) return null;
  
  // Count only active items (PENDING or PROCESSING)
  const activeCount = items.filter(
    (it) => it.status === 'PENDING' || it.status === 'PROCESSING'
  ).length;
  
  return (
    <div className="border border-ink bg-white" data-testid="batch-list">
      <div className="flex items-center justify-between border-b border-ink bg-ink px-3 py-2 text-white">
        <p className="font-mono text-[10px] uppercase tracking-[0.25em]">Batch queue</p>
        <p className="font-mono text-[10px] uppercase tracking-widest">{activeCount} active</p>
      </div>
      <ul className="divide-y divide-border">
        {items.map((it) => {
          const done = it.status === 'COMPLETED';
          const failed = it.status === 'FAILED';
          const active = it.tempId === activeId || it.result?.id === activeId;
          return (
            <li
              key={it.tempId}
              onClick={() => onSelect?.(it)}
              className={`cursor-pointer px-3 py-2 hover:bg-surfaceMuted transition-colors ${active ? 'bg-surfaceMuted' : ''}`}
              data-testid={`batch-item-${it.tempId}`}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="truncate font-mono text-xs">{it.filename}</p>
                <span className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-widest text-inkSecondary">
                  {STATUS_ICON[it.status]} {it.status}
                </span>
              </div>
              <div className="mt-2 relative h-[2px] w-full overflow-hidden bg-border">
                {it.status === 'PROCESSING' && <div className="progress-indeterminate absolute inset-0" />}
                {done && <div className="absolute inset-0 bg-confHigh" style={{ width: '100%' }} />}
                {failed && <div className="absolute inset-0 bg-confLow" style={{ width: '100%' }} />}
              </div>
              {done && it.result?.traveller?.full_name?.value && (
                <p className="mt-1 truncate font-mono text-[10px] text-inkSecondary">
                  {it.result.document_type} · {it.result.traveller.full_name.value}
                </p>
              )}
              {failed && it.error && (
                <p className="mt-1 truncate font-mono text-[10px] text-confLow">{it.error}</p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}