// frontend/app/page.js
'use client';

import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Copy, DownloadSimple, Fingerprint } from '@phosphor-icons/react';

import UploadZone from '@/components/UploadZone';
import BatchList from '@/components/BatchList';
import DocumentPreview from '@/components/DocumentPreview';
import ExtractedData from '@/components/ExtractedData';
import DuplicateAlert from '@/components/DuplicateAlert';
import HistoryTable from '@/components/HistoryTable';
import { ConfidenceBadge } from '@/components/ConfidenceBadge';
import { uploadSingle, uploadBatch, health, getDocument, jsonDownloadUrl } from '@/lib/api';

function newTempId() {
  return `tmp_${Math.random().toString(36).slice(2, 10)}`;
}

export default function Home() {
  const [health_, setHealth] = useState(null);
  const [items, setItems] = useState([]); // {tempId, filename, status, file?, result?, error?, uploaded?: boolean}
  const [activeTempId, setActiveTempId] = useState(null);
  const [selectedHistoryDoc, setSelectedHistoryDoc] = useState(null); // {id, filename, result, status}
  const [processing, setProcessing] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);

  useEffect(() => {
    health().then(setHealth).catch(() => setHealth({ ocr_ready: false, status: 'down' }));
  }, []);

  const activeItem = items.find((i) => i.tempId === activeTempId) || null;
  const activeResult = activeItem?.result || null;
  
  // Use history document if selected, otherwise use active upload result
  const displayResult = selectedHistoryDoc?.result || activeResult;
  const displayFilename = selectedHistoryDoc?.filename || activeItem?.filename || null;

  const handleFiles = async (files) => {
    if (!files?.length) return;
    
    // Clear any selected history document when a new upload begins
    setSelectedHistoryDoc(null);
    setProcessing(true);
    
    const newItems = files.map((f) => ({
      tempId: newTempId(),
      filename: f.name,
      status: 'PROCESSING',
      file: f,
      uploaded: true, // Mark as uploaded in current session
    }));
    setItems((prev) => [...newItems, ...prev]);
    if (newItems[0]) setActiveTempId(newItems[0].tempId);

    try {
      if (newItems.length === 1) {
        const it = newItems[0];
        try {
          const result = await uploadSingle(it.file);
          setItems((prev) => prev.map((p) => p.tempId === it.tempId ? { ...p, status: result.status, result } : p));
          if (result.duplicates?.length) toast.warning(`Duplicate detected: ${result.duplicates.length} match(es)`);
          else toast.success('Document processed');
          
          // Schedule removal for this specific uploaded item after 3 seconds
          setTimeout(() => {
            setItems((prev) => {
              // Only remove if it's still in the list and was uploaded (not from history)
              const itemExists = prev.find((p) => p.tempId === it.tempId);
              if (itemExists && itemExists.uploaded) {
                // If this was the active item, clear or switch selection
                setActiveTempId((current) => {
                  if (current === it.tempId) {
                    const remaining = prev.filter((p) => p.tempId !== it.tempId);
                    if (remaining.length > 0) {
                      const activeRemaining = remaining.find(
                        (p) => p.status === 'PENDING' || p.status === 'PROCESSING'
                      ) || remaining[0];
                      return activeRemaining.tempId;
                    }
                    return null;
                  }
                  return current;
                });
                return prev.filter((p) => p.tempId !== it.tempId);
              }
              return prev;
            });
          }, 3000);
        } catch (e) {
          setItems((prev) => prev.map((p) => p.tempId === it.tempId ? { ...p, status: 'FAILED', error: e.message } : p));
          toast.error(`Failed: ${e.message}`);
          
          // Schedule removal for failed item too
          setTimeout(() => {
            setItems((prev) => {
              const itemExists = prev.find((p) => p.tempId === it.tempId);
              if (itemExists && itemExists.uploaded) {
                setActiveTempId((current) => {
                  if (current === it.tempId) {
                    const remaining = prev.filter((p) => p.tempId !== it.tempId);
                    if (remaining.length > 0) {
                      const activeRemaining = remaining.find(
                        (p) => p.status === 'PENDING' || p.status === 'PROCESSING'
                      ) || remaining[0];
                      return activeRemaining.tempId;
                    }
                    return null;
                  }
                  return current;
                });
                return prev.filter((p) => p.tempId !== it.tempId);
              }
              return prev;
            });
          }, 3000);
        }
      } else {
        try {
          const batch = await uploadBatch(newItems.map((it) => it.file));
          // Match backend results to temp items in order (backend preserves order)
          setItems((prev) => {
            const updated = [...prev];
            batch.results.forEach((r, idx) => {
              const target = newItems[idx];
              const i = updated.findIndex((x) => x.tempId === target.tempId);
              if (i >= 0) updated[i] = { ...updated[i], status: r.status, result: r, error: r.error };
            });
            return updated;
          });
          toast.success(`Batch complete · ${batch.completed}/${batch.total}`);
          
          // Schedule removal for all uploaded items after 3 seconds
          setTimeout(() => {
            setItems((prev) => {
              const remaining = prev.filter((p) => {
                // Keep items that are not from this upload session
                const isFromThisUpload = newItems.some((n) => n.tempId === p.tempId);
                return !isFromThisUpload;
              });
              
              // Update active selection if needed
              if (remaining.length === 0) {
                setActiveTempId(null);
              } else {
                setActiveTempId((current) => {
                  // If current active item was removed, pick first remaining
                  if (current && !remaining.find((p) => p.tempId === current)) {
                    return remaining[0].tempId;
                  }
                  return current;
                });
              }
              
              return remaining;
            });
          }, 3000);
        } catch (e) {
          setItems((prev) => prev.map((p) => newItems.some((n) => n.tempId === p.tempId) ? { ...p, status: 'FAILED', error: e.message } : p));
          toast.error(`Batch failed: ${e.message}`);
          
          // Schedule removal for failed items too
          setTimeout(() => {
            setItems((prev) => {
              const remaining = prev.filter((p) => {
                const isFromThisUpload = newItems.some((n) => n.tempId === p.tempId);
                return !isFromThisUpload;
              });
              
              if (remaining.length === 0) {
                setActiveTempId(null);
              } else {
                setActiveTempId((current) => {
                  if (current && !remaining.find((p) => p.tempId === current)) {
                    return remaining[0].tempId;
                  }
                  return current;
                });
              }
              
              return remaining;
            });
          }, 3000);
        }
      }
      setHistoryKey((k) => k + 1);
    } finally {
      setProcessing(false);
    }
  };

  const handleCopyJson = async () => {
    if (!displayResult) return;
    await navigator.clipboard.writeText(JSON.stringify(displayResult, null, 2));
    toast.success('JSON copied to clipboard');
  };

  const handleHistorySelect = async (row) => {
    try {
      const full = await getDocument(row.id);
      // Store history document in separate state, not in items
      setSelectedHistoryDoc({
        id: full.id,
        filename: full.filename,
        result: full,
        status: full.status,
      });
      // Clear any active upload selection to show history document
      setActiveTempId(null);
    } catch (e) {
      toast.error(`Cannot open record: ${e.message}`);
    }
  };

  // Clear history selection when clicking a batch item
  const handleBatchSelect = (item) => {
    setSelectedHistoryDoc(null);
    setActiveTempId(item.tempId);
  };

  return (
    <main className="min-h-screen">
      {/* Top bar */}
      <header className="border-b border-ink bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-4 py-3 md:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center border border-ink bg-ink">
              <Fingerprint size={16} weight="bold" className="text-white" />
            </div>
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-inkSecondary">Traveller / Docs</p>
              <h1 className="font-sans text-lg font-semibold tracking-tight">
                Document Processing System
              </h1>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-3" data-testid="health-strip">
            <div className="hidden md:flex items-center gap-2 border border-border px-2 py-1">
              <span className={`h-2 w-2 ${health_?.ocr_ready ? 'bg-confHigh' : 'bg-confLow'}`} />
              <span className="font-mono text-[10px] uppercase tracking-widest">
                OCR {health_?.ocr_ready ? 'ready' : 'offline'}
              </span>
            </div>
            {health_?.languages && (
              <div className="hidden md:flex items-center gap-2 border border-border px-2 py-1">
                <span className="font-mono text-[10px] uppercase tracking-widest text-inkSecondary">Lang</span>
                <span className="font-mono text-[10px] uppercase tracking-widest">{health_.languages.join(' · ')}</span>
              </div>
            )}
            <div className="hidden md:flex items-center gap-2 border border-border px-2 py-1">
              <span className="font-mono text-[10px] uppercase tracking-widest text-inkSecondary">DB</span>
              <span className="font-mono text-[10px] uppercase tracking-widest">{health_?.database || '—'}</span>
            </div>
          </div>
        </div>
      </header>

      {/* Hero / stats */}
      <section className="border-b border-border bg-surfaceMuted blueprint-grid">
        <div className="mx-auto max-w-7xl px-4 py-10 md:px-8">
          <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-inkSecondary">
            Offline · PaddleOCR · OpenCV · PDF · Passport · Aadhaar · PAN · DL · Voter ID
          </p>
          <h2 className="mt-3 max-w-3xl font-sans text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
            Extract, verify and deduplicate identity documents at scale.
          </h2>
          <p className="mt-3 max-w-2xl font-body text-base text-inkSecondary">
            Upload single or batch documents - passports, Aadhaar, PAN, driving
            licences, voter ID or any other government ID. The engine classifies,
            OCRs, normalises, validates and deduplicates every record - with
            per-field confidence and a downloadable JSON contract.
          </p>
        </div>
      </section>

      {/* Workspace */}
      <section className="mx-auto max-w-7xl px-4 py-8 md:px-8">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          {/* Left: upload + batch queue */}
          <div className="space-y-4 lg:col-span-4">
            <UploadZone onFiles={handleFiles} disabled={processing} />
            <BatchList
              items={items}
              activeId={activeTempId}
              onSelect={handleBatchSelect}
            />
          </div>

          {/* Middle: preview */}
          <div className="lg:col-span-4">
            <DocumentPreview file={activeItem?.file} />
          </div>

          {/* Right: extracted data */}
          <div className="space-y-4 lg:col-span-4">
            {displayResult ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="truncate">
                    <p className="data-label">Document</p>
                    <p className="data-value mt-1 truncate">{displayFilename}</p>
                    {selectedHistoryDoc && (
                      <p className="font-mono text-[10px] uppercase tracking-widest text-inkMuted mt-1">
                        From History
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <a
                      href={jsonDownloadUrl(displayResult.id)}
                      className="inline-flex items-center gap-2 border border-ink px-3 py-2 font-mono text-[11px] uppercase tracking-widest hover:bg-ink hover:text-white transition-colors"
                      data-testid="download-json-button"
                    >
                      <DownloadSimple size={14} /> Download JSON
                    </a>
                    <button
                      type="button"
                      onClick={handleCopyJson}
                      className="inline-flex items-center gap-2 border border-ink px-3 py-2 font-mono text-[11px] uppercase tracking-widest hover:bg-ink hover:text-white transition-colors"
                      data-testid="copy-json-button"
                    >
                      <Copy size={14} /> Copy JSON
                    </button>
                  </div>
                </div>

                {displayResult.duplicates?.length > 0 && (
                  <DuplicateAlert
                    duplicates={displayResult.duplicates}
                    onSelect={(id) => handleHistorySelect({ id })}
                  />
                )}

                <ExtractedData result={displayResult} />

                <div className="border border-border">
                  <div className="border-b border-border bg-surfaceMuted px-3 py-2">
                    <p className="data-label">OCR Metadata</p>
                  </div>
                  <dl className="grid grid-cols-2 divide-x divide-border border-b border-border">
                    <div className="p-3">
                      <p className="data-label">Engine</p>
                      <p className="data-value mt-1">{displayResult.ocr_metadata.engine}</p>
                    </div>
                    <div className="p-3">
                      <p className="data-label">Languages</p>
                      <p className="data-value mt-1">{displayResult.ocr_metadata.languages.join(', ')}</p>
                    </div>
                    <div className="p-3 border-t border-border">
                      <p className="data-label">Avg Confidence</p>
                      <div className="mt-1"><ConfidenceBadge value={displayResult.ocr_metadata.average_confidence} dotOnly /></div>
                    </div>
                    <div className="p-3 border-t border-border">
                      <p className="data-label">Tokens</p>
                      <p className="data-value mt-1">{displayResult.ocr_metadata.token_count}</p>
                    </div>
                    <div className="p-3 border-t border-border">
                      <p className="data-label">Processing (ms)</p>
                      <p className="data-value mt-1">{displayResult.ocr_metadata.processing_ms}</p>
                    </div>
                    <div className="p-3 border-t border-border">
                      <p className="data-label">Rotation</p>
                      <p className="data-value mt-1">{displayResult.ocr_metadata.rotation_applied_degrees}°</p>
                    </div>
                  </dl>
                </div>
              </>
            ) : (
              <div className="border border-dashed border-borderStrong bg-white p-8 blueprint-grid">
                <p className="data-label">Awaiting document</p>
                <p className="mt-2 font-sans text-lg font-medium tracking-tight text-inkSecondary">
                  Upload a document to view the extracted traveller data here.
                </p>
                <ul className="mt-4 space-y-2 font-mono text-[11px] uppercase tracking-widest text-inkMuted">
                  <li>1 · Drop a passport, Aadhaar, PAN, DL, voter ID or PDF</li>
                  <li>2 · Watch the batch queue on the left</li>
                  <li>3 · Review extracted data + confidence</li>
                  <li>4 · Export the structured JSON</li>
                </ul>
              </div>
            )}
          </div>
        </div>

        {/* History */}
        <div className="mt-10">
          <HistoryTable onSelect={handleHistorySelect} refreshKey={historyKey} />
        </div>
      </section>

      <footer className="border-t border-border bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 md:px-8">
          <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-inkMuted">
            Offline OCR · PaddleOCR · No API keys · No paid services
          </p>
          <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-inkMuted">
            v{health_?.app ? '1.0.0' : '—'}
          </p>
        </div>
      </footer>
    </main>
  );
}