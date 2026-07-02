'use client';

import { Warning } from '@phosphor-icons/react';

export default function DuplicateAlert({ duplicates, onSelect }) {
  if (!duplicates || duplicates.length === 0) return null;
  return (
    <div
      className="border border-dupBorder bg-dupBg p-4"
      data-testid="duplicate-alert"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <Warning size={20} weight="bold" className="mt-0.5 text-dupText" />
          <div>
            <p className="font-mono text-[11px] uppercase tracking-widest text-dupText">
              Duplicate traveller detected
            </p>
            <p className="text-sm text-dupText">
              This document matches {duplicates.length} previously processed record{duplicates.length > 1 ? 's' : ''}.
            </p>
          </div>
        </div>
      </div>
      <ul className="mt-3 divide-y divide-dupBorder border border-dupBorder bg-white">
        {duplicates.map((d) => (
          <li
            key={d.document_id}
            className="flex items-center justify-between gap-4 px-3 py-2"
            data-testid={`duplicate-record-${d.document_id}`}
          >
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-[12px]">
                {d.full_name || '—'} <span className="text-inkMuted">·</span> {d.document_number || '—'}
              </p>
              <p className="font-mono text-[10px] uppercase tracking-widest text-inkSecondary">
                {d.document_type} · {d.match_type} · {Math.round(d.score * 100)}% · via {d.matched_on.join(', ')}
              </p>
            </div>
            {onSelect && (
              <button
                type="button"
                onClick={() => onSelect(d.document_id)}
                className="border border-ink bg-white px-3 py-1 font-mono text-[10px] uppercase tracking-widest hover:bg-ink hover:text-white transition-colors"
                data-testid={`duplicate-view-${d.document_id}`}
              >
                View matching record
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
