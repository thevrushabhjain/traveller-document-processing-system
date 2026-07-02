'use client';

import { FileText } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

export default function DocumentPreview({ file, previewUrl }) {
  const [objectUrl, setObjectUrl] = useState(null);

  useEffect(() => {
    if (!file) return undefined;
    const url = URL.createObjectURL(file);
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const url = objectUrl || previewUrl;
  const isPdf = file ? file.type === 'application/pdf' : previewUrl?.toLowerCase().endsWith('.pdf');

  if (!url) {
    return (
      <div
        data-testid="document-preview-empty"
        className="relative flex h-[60vh] items-center justify-center border border-borderStrong bg-surfaceMuted blueprint-grid stamp-overlay"
      >
        <div className="relative z-10 flex flex-col items-center gap-3">
          <FileText size={32} className="text-inkMuted" />
          <p className="font-mono text-[11px] uppercase tracking-[0.25em] text-inkMuted">
            No document loaded
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative border border-ink bg-white" data-testid="document-preview">
      <div className="flex items-center justify-between border-b border-border bg-surfaceMuted px-3 py-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-inkSecondary">
          Document preview
        </p>
        <span className="font-mono text-[10px] uppercase tracking-widest text-inkMuted">
          {isPdf ? 'PDF' : 'IMAGE'}
        </span>
      </div>
      <div className="max-h-[70vh] overflow-auto">
        {isPdf ? (
          <object data={url} type="application/pdf" className="h-[70vh] w-full">
            <p className="p-4 font-mono text-xs text-inkSecondary">
              PDF preview unavailable — <a href={url} className="underline" target="_blank" rel="noreferrer">open in a new tab</a>.
            </p>
          </object>
        ) : (
          <img src={url} alt="preview" className="mx-auto block max-w-full" />
        )}
      </div>
    </div>
  );
}
