// Thin API client for the Traveller Document Processing backend.
// Requests are sent directly to the FastAPI server using the public base
// URL (this environment's ingress routes any `/api/*` path straight to
// the backend regardless of which host served the frontend page).

const BASE = "http://127.0.0.1:8000";

async function _handle(res) {
  if (!res.ok) {
    let detail;
    try {
      detail = await res.json();
    } catch {
      detail = { message: res.statusText };
    }
    const err = new Error(detail.detail || detail.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = detail;
    throw err;
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export async function health() {
  const res = await fetch(`${BASE}/api/health`, { cache: 'no-store' });
  return _handle(res);
}

export async function uploadSingle(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${BASE}/api/documents/upload`, { method: 'POST', body: fd });
  return _handle(res);
}

export async function uploadBatch(files) {
  const fd = new FormData();
  files.forEach((f) => fd.append('files', f));
  const res = await fetch(`${BASE}/api/documents/batch`, { method: 'POST', body: fd });
  return _handle(res);
}

export async function listDocuments({ limit = 25, offset = 0, search = '', type = '' } = {}) {
  const params = new URLSearchParams();
  params.set('limit', limit);
  params.set('offset', offset);
  if (search) params.set('search', search);
  if (type) params.set('document_type', type);
  const res = await fetch(`${BASE}/api/documents?${params.toString()}`, { cache: 'no-store' });
  return _handle(res);
}

export async function getDocument(id) {
  const res = await fetch(`${BASE}/api/documents/${id}`, { cache: 'no-store' });
  return _handle(res);
}

export async function deleteDocument(id) {
  const res = await fetch(`${BASE}/api/documents/${id}`, { method: 'DELETE' });
  return _handle(res);
}

export function jsonDownloadUrl(id) {
  return `${BASE}/api/documents/${id}/json`;
}

export function originalFileUrl(id) {
  return `${BASE}/api/documents/${id}/file`;
}
