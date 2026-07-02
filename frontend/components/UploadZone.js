// frontend/components/UploadZone.js
'use client';

import { useCallback, useRef, useState } from 'react';
import { UploadSimple, FilePdf, Image as ImageIcon } from '@phosphor-icons/react';
import { cn } from '@/lib/cn';

const ACCEPT = 'image/*,application/pdf';

export default function UploadZone({ onFiles, disabled, multiple = true, testId = 'upload-dropzone' }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  const handleFiles = useCallback(
    (files) => {
      const arr = Array.from(files || []).filter(Boolean);
      if (!arr.length) return;
      onFiles?.(multiple ? arr : arr.slice(0, 1));
    },
    [onFiles, multiple],
  );

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (disabled) return;
    handleFiles(e.dataTransfer.files);
    // Reset file input after drop
    if (inputRef.current) {
      inputRef.current.value = '';
    }
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => !disabled && inputRef.current?.click()}
      data-testid={testId}
      className={cn(
        'relative cursor-pointer select-none border border-dashed transition-colors duration-150',
        'blueprint-grid bg-surfaceMuted',
        dragging ? 'border-accent border-solid bg-white' : 'border-borderStrong',
        disabled && 'opacity-60 pointer-events-none',
      )}
    >
      <div className="flex flex-col items-center justify-center gap-4 px-6 py-14 text-center">
        <div className="flex h-12 w-12 items-center justify-center border border-ink bg-white">
          <UploadSimple size={22} weight="regular" />
        </div>
        <div className="space-y-1">
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-inkSecondary">
            Drop passport, Aadhaar, PAN, DL, voter ID or PDF here
          </p>
          <p className="font-sans text-lg font-semibold tracking-tight text-ink">
            {multiple ? 'Upload single or batch documents' : 'Upload a document'}
          </p>
          <p className="font-mono text-[11px] text-inkMuted">
            Supported · JPG · PNG · TIFF · WEBP · PDF · Max 25 MB per file
          </p>
        </div>
        <button
          type="button"
          disabled={disabled}
          onClick={(e) => {
            e.stopPropagation();
            inputRef.current?.click();
          }}
          className="border border-ink bg-ink px-4 py-2 font-mono text-xs uppercase tracking-widest text-white hover:bg-accent hover:border-accent transition-colors"
          data-testid="upload-select-files-button"
        >
          Select files
        </button>
        <div className="flex items-center gap-6 pt-2 font-mono text-[10px] uppercase tracking-widest text-inkMuted">
          <span className="inline-flex items-center gap-1"><ImageIcon size={14} /> Images</span>
          <span className="inline-flex items-center gap-1"><FilePdf size={14} /> PDF</span>
        </div>
      </div>
      <input
        ref={inputRef}
        data-testid="upload-file-input"
        type="file"
        multiple={multiple}
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          handleFiles(e.target.files);
          // Reset the input value so the same file can be selected again
          e.target.value = '';
        }}
      />
    </div>
  );
}