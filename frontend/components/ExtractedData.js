'use client';

import { ConfidenceBadge } from './ConfidenceBadge';

const FIELDS = [
  { key: 'document_number', label: 'Document No.' },
  { key: 'full_name', label: 'Full Name' },
  { key: 'given_names', label: 'Given Names' },
  { key: 'surname', label: 'Surname' },
  { key: 'father_name', label: 'Father / Guardian' },
  { key: 'date_of_birth', label: 'Date of Birth' },
  { key: 'gender', label: 'Gender' },
  { key: 'nationality', label: 'Nationality' },
  { key: 'country', label: 'Country' },
  { key: 'country_code', label: 'Country Code' },
  { key: 'place_of_birth', label: 'Place of Birth' },
  { key: 'date_of_issue', label: 'Date of Issue' },
  { key: 'date_of_expiry', label: 'Date of Expiry' },
  { key: 'issuing_authority', label: 'Issuing Authority' },
  { key: 'address', label: 'Address', wide: true },
  { key: 'mrz_line1', label: 'MRZ Line 1', wide: true, mono: true },
  { key: 'mrz_line2', label: 'MRZ Line 2', wide: true, mono: true },
];

function Cell({ label, field, wide, mono, testKey }) {
  const value = field?.value;
  return (
    <div
      className={`border border-border p-3 ${wide ? 'sm:col-span-2 lg:col-span-3' : ''}`}
      data-testid={`extracted-cell-${testKey}`}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="data-label" data-testid={`extracted-label-${testKey}`}>{label}</span>
        {field && <ConfidenceBadge value={field.confidence} dotOnly testId={`extracted-confidence-${testKey}`} />}
      </div>
      <p
        className={`mt-2 text-sm ${mono ? 'font-mono break-all' : 'data-value'} ${!value ? 'text-inkMuted italic' : ''}`}
        data-testid={`extracted-value-${testKey}`}
      >
        {value || '—'}
      </p>
    </div>
  );
}

export default function ExtractedData({ result }) {
  if (!result) return null;
  const t = result.traveller || {};
  const validations = result.validation || [];
  const extras = Object.entries(t.additional_fields || {}).filter(([, v]) => v?.value);

  return (
    <section className="space-y-4" data-testid="extracted-data-section">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-ink pb-3">
        <div>
          <p className="data-label">Extracted Traveller Data</p>
          <h2 className="mt-1 font-sans text-2xl font-semibold tracking-tight">
            {result.filename}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <span className="border border-ink bg-ink px-2 py-1 font-mono text-[11px] uppercase tracking-widest text-white" data-testid="document-type-badge">
            {result.document_type}
          </span>
          <ConfidenceBadge value={result.classification_confidence} testId="classification-confidence-badge" />
          <ConfidenceBadge value={result.overall_confidence} testId="overall-confidence-badge" />
        </div>
      </header>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
        {FIELDS.map((f) => (
          <Cell key={f.key} label={f.label} field={t[f.key]} wide={f.wide} mono={f.mono} testKey={f.key} />
        ))}
      </div>

      {extras.length > 0 && (
        <div className="border border-border" data-testid="additional-fields-section">
          <div className="border-b border-border bg-surfaceMuted px-3 py-2">
            <p className="data-label">Additional Metadata</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
            {extras.map(([key, field]) => (
              <Cell key={key} label={key.replace(/_/g, ' ')} field={field} testKey={`extra-${key}`} />
            ))}
          </div>
        </div>
      )}

      {validations.length > 0 && (
        <div className="border border-border" data-testid="validation-issues">
          <div className="border-b border-border bg-surfaceMuted px-3 py-2">
            <p className="data-label">Validation Issues</p>
          </div>
          <ul className="divide-y divide-border">
            {validations.map((v, i) => (
              <li key={i} className="flex items-start justify-between gap-4 px-3 py-2">
                <div>
                  <p className="font-mono text-[11px] uppercase tracking-widest text-inkSecondary">{v.field}</p>
                  <p className="text-sm">{v.message}</p>
                </div>
                <span
                  className={`px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest ${
                    v.severity === 'ERROR'
                      ? 'border border-confLow text-confLow'
                      : v.severity === 'WARNING'
                        ? 'border border-confMid text-confMid'
                        : 'border border-inkSecondary text-inkSecondary'
                  }`}
                >
                  {v.severity}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
