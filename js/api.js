// The only fetch wrapper. Every piece of product data reaches the browser through these calls.

export class ApiError extends Error {
  constructor(status, body) {
    const e = (body && body.error) || {};
    super(e.message || `Request failed (${status})`);
    this.status = status;
    this.code = e.code || 'network';
    this.retryAfter = e.retry_after_s ?? null;
  }
}

// Dev only: the next request whose path matches `match` gets `?force=<code>` (honoured when FA_HOSTED is false).
let forced = null;
export function forceNext(match, code) { forced = match ? { match, code } : null; }

// Static build (GitHub Pages, tools/build_static.py): <html data-static> — reads come from files dumped from the real
// server, replays answer checks from their recorded results, live mode and uploads need the server.
const STATIC = typeof document !== 'undefined' && document.documentElement.hasAttribute('data-static');
const sessions = {};
const frameIndex = {};
const file = (path) => '.' + path + (path.startsWith('/legal/') ? '' : path.endsWith('/timeline') ? '.jsonl' : '.json');
async function get(path) {
  const res = await fetch(file(path));
  if (!res.ok) throw new ApiError(res.status, { error: { code: 'not_found', message: 'Not in the static demo.' } });
  return path.startsWith('/legal/') || path.endsWith('/timeline') ? res.text() : res.json();
}
async function staticRequest(method, path, json, form) {
  if (method === 'GET') return get(path);
  const needServer = () => { throw new ApiError(503, { error: { code: 'no_api_key',
    message: 'This is the static demo: replays only. Run the app with a Meta API key for live mode (see GitHub).' } }); };
  if (path === '/api/sessions') {
    if (json.mode !== 'replay') needServer();
    const sid = `static-${Math.random().toString(36).slice(2)}`;
    sessions[sid] = { rid: json.recording_id, results: await get(`/api/recordings/${json.recording_id}/results`) };
    return { session_id: sid };
  }
  const [, sid, action] = path.match(/^\/api\/sessions\/([^/]+)\/(\w+)$/) || [];
  const s = sessions[sid];
  if (!s) needServer();
  if (action === 'checks') {
    const m = JSON.parse(form.get('meta'));
    const hit = s.results.find((r) => r.step_id === m.step_id && r.kind === m.kind && r.check_no === m.check_no);
    if (!hit) throw new ApiError(404, { error: { code: 'not_found', message: `No recorded result for step ${m.step_id}.` } });
    return structuredClone(hit.result);
  }
  if (action === 'end') return get(`/api/recordings/${s.rid}/summary`);
  if (action === 'questions') return { answer: 'Questions need the live app.', latency_ms: 0 };
  return null; // events: nothing to store
}

async function request(method, path, { json, form, blob } = {}) {
  if (STATIC) return staticRequest(method, path, json, form);
  let url = path;
  if (forced && new RegExp(forced.match).test(`${method} ${path}`)) {
    url += (url.includes('?') ? '&' : '?') + 'force=' + encodeURIComponent(forced.code);
    forced = null;
  }
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (json !== undefined) { opts.body = JSON.stringify(json); opts.headers['Content-Type'] = 'application/json'; }
  if (form) opts.body = form;
  if (blob) { opts.body = blob; opts.headers['Content-Type'] = blob.type; }
  // Never hang: a server that stops answering becomes a clear error (uploads of a whole video get much longer)
  const ctrl = new AbortController();
  const limit = method === 'POST' && path === '/api/tutorials' ? 600000 : 30000;
  const timer = setTimeout(() => ctrl.abort(), limit);
  opts.signal = ctrl.signal;
  let res;
  try { res = await fetch(url, opts); } catch (err) {
    clearTimeout(timer);
    if (err?.name === 'AbortError') throw new ApiError(0, { error: { code: 'timeout', message: "The FollowAlong server isn't answering. Check it's running, then try again." } });
    throw new ApiError(0, { error: { code: 'network', message: "Can't reach the FollowAlong server." } });
  }
  clearTimeout(timer);
  if (res.status === 204) return null;
  const type = res.headers.get('content-type') || '';
  const body = type.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, typeof body === 'object' ? body : null);
  return body;
}

const multipart = (fields) => { const f = new FormData(); for (const [k, v] of Object.entries(fields)) f.append(k, ...[].concat(v)); return f; };
const enc = encodeURIComponent;

export const api = {
  config: () => request('GET', '/api/config'),
  tutorials: () => request('GET', '/api/tutorials'),
  tutorial: (id) => request('GET', `/api/tutorials/${enc(id)}`),
  upload: (file) => request('POST', '/api/tutorials', { form: multipart({ file: [file, file.name] }) }),
  // CR-006: one PDF or page photos in order
  uploadManual: (files) => { const f = new FormData(); f.append('kind', 'manual'); for (const x of files) f.append('files', x, x.name); return request('POST', '/api/tutorials', { form: f }); },
  uploadLink: (url) => request('POST', '/api/tutorials', { form: multipart({ url }) }),
  deleteTutorial: (id) => request('DELETE', `/api/tutorials/${enc(id)}`),
  plan: (id) => request('GET', `/api/tutorials/${enc(id)}/plan`),
  details: (id, lang = 'en') => request('GET', `/api/tutorials/${enc(id)}/details${lang !== 'en' && !STATIC ? `?lang=${enc(lang)}` : ''}`),
  phrases: (lang = 'en') => (STATIC ? Promise.resolve(null) : request('GET', `/api/phrases?lang=${enc(lang)}`)),
  deleteMe: () => request('DELETE', '/api/me'),
  metrics: () => request('GET', '/api/metrics'),
  clientError: (message, where) => (STATIC ? null : fetch('/api/client-errors', { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message, where }) }).catch(() => {})),
  savePlan: (id, plan) => request('PUT', `/api/tutorials/${enc(id)}/plan`, { json: plan }),
  videoUrl: (id) => STATIC ? `./api/tutorials/${enc(id)}/video.mp4` : `/api/tutorials/${enc(id)}/video`,
  frameUrl: (id, step) => STATIC ? `./api/tutorials/${enc(id)}/frames/${step}.jpg` : `/api/tutorials/${enc(id)}/frames/${step}`,
  startSession: (body) => request('POST', '/api/sessions', { json: body }),
  check: (sid, frame, meta) => request('POST', `/api/sessions/${enc(sid)}/checks`,
    { form: multipart({ frame: [frame, 'frame.jpg'], meta: JSON.stringify(meta) }) }),
  watch: (sid, frame, meta) => request('POST', `/api/sessions/${enc(sid)}/watch`,
    { form: multipart({ frame: [frame, 'frame.jpg'], meta: JSON.stringify(meta) }) }),
  question: (sid, frame, meta) => request('POST', `/api/sessions/${enc(sid)}/questions`,
    { form: multipart({ frame: [frame, 'frame.jpg'], meta: JSON.stringify(meta) }) }),
  transcribe: (sid, wavBlob) => request('POST', `/api/sessions/${enc(sid)}/transcribe`, { blob: wavBlob }),
  event: (sid, ev) => request('POST', `/api/sessions/${enc(sid)}/events`, { json: ev }),
  endSession: (sid, reason) => request('POST', `/api/sessions/${enc(sid)}/end`, { json: { reason } }),
  endBeacon: (sid) => !STATIC && navigator.sendBeacon(`/api/sessions/${enc(sid)}/end`, new Blob([JSON.stringify({ reason: 'left' })], { type: 'application/json' })),
  recordings: () => request('GET', '/api/recordings'),
  recordingFrameUrl: (id, ms) => {
    if (!STATIC) return `/api/recordings/${enc(id)}/frames/${Math.max(0, Math.floor(ms))}`;
    const at = (frameIndex[id] || []).filter((t) => t <= ms).pop() ?? (frameIndex[id] || [0])[0];
    return `./api/recordings/${enc(id)}/frames/preview-${at}.jpg`;
  },
  timeline: async (id) => {
    if (STATIC && !frameIndex[id]) frameIndex[id] = await get(`/api/recordings/${id}/frames`);
    return (await request('GET', `/api/recordings/${enc(id)}/timeline`)).split('\n').filter(Boolean).map((l) => JSON.parse(l));
  },
  creator: (id) => request('GET', `/api/creator/${enc(id)}`),
  evaluation: () => request('GET', '/api/evaluation/latest'),
  legal: (name) => request('GET', `/legal/${enc(name)}.md`), // static text: the privacy notice and terms (FR-31)
};
