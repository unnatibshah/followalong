// Where each lesson was left, in this browser only (localStorage): a reload, a phone call or a closed tab picks up at
// the same step. Nothing is sent anywhere. Entries older than a week are forgotten.
const KEY = 'fa.progress';
const WEEK = 7 * 86400000;

function all() {
  try {
    const p = JSON.parse(localStorage.getItem(KEY) || '{}');
    return Object.fromEntries(Object.entries(p).filter(([, v]) => v && Date.now() - v.at < WEEK));
  } catch { return {}; }
}
function write(p) { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* storage blocked: no resume */ } }

export function saveProgress(tid, step, total, title) { write({ ...all(), [tid]: { step, total, title, at: Date.now() } }); }
export function clearProgress(tid) { const p = all(); delete p[tid]; write(p); }
/** { step, total, title, at } for an unfinished lesson past step 1, else null. */
export function progressOf(tid) { const p = all()[tid]; return p && p.step > 1 ? p : null; }
/** Unfinished lessons, newest first: [{ id, step, total, title, at }]. */
export function unfinished() {
  return Object.entries(all()).filter(([, v]) => v.step > 1).map(([id, v]) => ({ id, ...v })).sort((a, b) => b.at - a.at);
}

// Activity: every finished or ended session, newest first, on this device only (no account yet). Capped, so it
// never grows without bound. { id (session), tid, title, at, mode, steps_done, steps_total, duration_s, mistakes, hardest }
const RUNS = 'fa.runs';
const MAX_RUNS = 200;
export function runs() {
  try { const r = JSON.parse(localStorage.getItem(RUNS) || '[]'); return Array.isArray(r) ? r : []; } catch { return []; }
}
export function saveRun(run) {
  if (!run?.id) return;
  const rest = runs().filter((r) => r.id !== run.id); // a summary shown twice is one run
  try { localStorage.setItem(RUNS, JSON.stringify([{ ...run, at: run.at || Date.now() }, ...rest].slice(0, MAX_RUNS))); } catch { /* storage blocked */ }
}

export function forgetAll() { try { localStorage.removeItem(KEY); localStorage.removeItem(RUNS); } catch { /* nothing kept */ } }
