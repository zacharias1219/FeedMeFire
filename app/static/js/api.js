let csrf = null;
export const setCsrf = (t) => { csrf = t; };
export const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));

export class ApiError extends Error {
  constructor(err, status) {
    super(err.message || 'Something went wrong.');
    this.code = err.code || 'INTERNAL_ERROR';
    this.status = status;
    this.retryable = !!err.retryable;
    this.details = err.details || null;
  }
}

export async function api(method, path, body, headers = {}) {
  const opts = { method, credentials: 'same-origin', headers: { ...headers } };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  if (csrf && method !== 'GET') opts.headers['X-CSRF-Token'] = csrf;
  let r;
  try { r = await fetch(path, opts); }
  catch { throw new ApiError({ code: 'NETWORK', message: 'Network problem. Check your connection and try again.', retryable: true }, 0); }
  if (r.status === 204) return null;
  let data = null;
  try { data = await r.json(); } catch { /* non-JSON error body */ }
  if (!r.ok) throw new ApiError((data && data.error) || { code: 'INTERNAL_ERROR', message: 'Something went wrong.', retryable: true }, r.status);
  return data;
}
