'use client';

import { useEffect, useState } from 'react';
import { MagnifyingGlass, Trash, DownloadSimple } from '@phosphor-icons/react';
import { listDocuments, deleteDocument, jsonDownloadUrl } from '@/lib/api';
import { ConfidenceBadge } from './ConfidenceBadge';
import { toast } from 'sonner';

const TYPES = ['', 'PASSPORT', 'AADHAAR', 'DRIVING_LICENSE', 'PAN', 'VOTER_ID', 'GENERIC'];

export default function HistoryTable({ onSelect, refreshKey }) {
  const [data, setData] = useState({ total: 0, items: [] });
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [type, setType] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const res = await listDocuments({ search, type, limit: 50 });
      setData(res);
    } catch (e) {
      toast.error(`Failed to load history: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, type]);

  const handleDelete = async (id) => {
    if (!confirm('Delete this record?')) return;
    try {
      await deleteDocument(id);
      toast.success('Record deleted');
      load();
    } catch (e) {
      toast.error(`Delete failed: ${e.message}`);
    }
  };

  return (
    <section className="border border-ink bg-white" data-testid="history-section">
      <div className="flex flex-wrap items-center gap-3 border-b border-ink bg-ink px-3 py-2 text-white">
        <p className="mr-auto font-mono text-[10px] uppercase tracking-[0.25em]">
          Processed documents ({data.total})
        </p>
        <div className="relative">
          <MagnifyingGlass size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-inkMuted" />
          <input
            data-testid="history-search"
            placeholder="Search name / number"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && load()}
            className="border border-border bg-white px-2 py-1 pl-6 font-mono text-[11px] text-ink placeholder:text-inkMuted focus:border-accent focus:outline-none"
          />
        </div>
        <select
          data-testid="history-type-filter"
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="border border-border bg-white px-2 py-1 font-mono text-[11px] text-ink focus:border-accent focus:outline-none"
        >
          {TYPES.map((t) => (
            <option key={t} value={t}>{t || 'ALL TYPES'}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={load}
          className="border border-white bg-transparent px-3 py-1 font-mono text-[10px] uppercase tracking-widest hover:bg-white hover:text-ink transition-colors"
          data-testid="history-refresh"
        >
          Refresh
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-border bg-surfaceMuted">
              {['Filename', 'Type', 'Number', 'Name', 'DOB', 'Confidence', ''].map((h) => (
                <th key={h} className="px-3 py-2 font-mono text-[10px] uppercase tracking-widest text-inkSecondary">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={7} className="px-3 py-6 text-center font-mono text-[11px] text-inkMuted">Loading…</td></tr>
            )}
            {!loading && data.items.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-6 text-center font-mono text-[11px] text-inkMuted">No processed documents yet.</td></tr>
            )}
            {data.items.map((it) => (
              <tr
                key={it.id}
                onClick={() => onSelect?.(it)}
                className="cursor-pointer border-b border-border hover:bg-surfaceMuted"
                data-testid={`history-row-${it.id}`}
              >
                <td className="px-3 py-2 font-mono text-xs">{it.filename}</td>
                <td className="px-3 py-2 font-mono text-[11px]">
                  <span className="border border-ink px-1.5 py-0.5">{it.document_type}</span>
                </td>
                <td className="px-3 py-2 font-mono text-xs">{it.traveller?.document_number?.value || '—'}</td>
                <td className="px-3 py-2 font-mono text-xs">{it.traveller?.full_name?.value || '—'}</td>
                <td className="px-3 py-2 font-mono text-xs">{it.traveller?.date_of_birth?.value || '—'}</td>
                <td className="px-3 py-2"><ConfidenceBadge value={it.overall_confidence} dotOnly /></td>
                <td className="px-3 py-2 text-right">
                  <div className="inline-flex items-center gap-1">
                    <a
                      href={jsonDownloadUrl(it.id)}
                      onClick={(e) => e.stopPropagation()}
                      className="border border-border p-1 hover:border-ink"
                      title="Download JSON"
                      data-testid={`history-download-${it.id}`}
                    >
                      <DownloadSimple size={12} />
                    </a>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); handleDelete(it.id); }}
                      className="border border-border p-1 hover:border-confLow hover:text-confLow"
                      title="Delete"
                      data-testid={`history-delete-${it.id}`}
                    >
                      <Trash size={12} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
