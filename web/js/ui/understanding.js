// S2 Understanding: polls GET /api/tutorials/{id} while the upload is processed; Cancel = DELETE.
import { h, clear } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { nav } from '../router.js';
import { showError } from '../overlays.js';

// TutorialMeta.progress is "<stage> <done>/<total>" (step-0 convention), e.g. "finding_steps 4/10".
const parse = (p) => { const m = /^(\w+) (\d+)\/(\d+)$/.exec(p || ''); return m ? { stage: m[1], done: +m[2], total: +m[3] } : null; };

export async function render(root) {
  if (!app.tutorialId) { nav(''); return null; }
  const sub = h('div', { class: 'sub' });
  const reading = h('span', { class: 'pill tr mono' });
  const segs = h('div', { class: 'proc-segs', 'aria-hidden': 'true' });
  const rows = h('div', { class: 'proc-rows' });
  const log = h('div', { class: 'proc-log' }, h('div', { class: 'log-head mono' }, h('span', {}, 'log'),
    h('span', {}, h('span', { class: 'c-spark' }, '● muse spark'), ' ', h('span', { class: 'c-sam' }, '● sam 3.1'))));
  const error = h('div', { class: 'note bad row', hidden: true },
    h('span', { class: 'fail-text' }, "We couldn't turn this video into steps. Try a shorter or clearer video."),
    h('button', { class: 'btn btn-accent', onclick: () => nav('') }, 'Try again'));
  const cancel = h('button', { class: 'btn btn-outline-dark', onclick: async () => {
    try { await api.deleteTutorial(app.tutorialId); } catch { /* already gone */ }
    nav('');
  } }, 'Cancel');

  const heading = h('h1', { class: 'title-serif' }, 'Watching your tutorial…');
  root.append(h('div', { class: 'wrap-wide split-proc' },
    h('div', { class: 'col' },
      heading, sub,
      // Its own URL: the end frames don't exist yet (the server sends a placeholder), and Chrome's image cache would
      // otherwise show that placeholder again on S3/S4 for the same URL.
      h('div', { class: 'media-frame scan-frame' }, h('img', { class: 'frame-img', src: `${api.frameUrl(app.tutorialId, 1)}?while=processing`, alt: '' }),
        h('div', { class: 'scan', 'aria-hidden': 'true' }), h('span', { class: 'pill tl mono' }, 'muted · 4×'), reading),
      segs, rows, error, h('div', { class: 'row-end' }, cancel)),
    log));

  let lastLine = '';
  let timer = 0, stopped = false;
  async function poll() {
    if (stopped) return;
    let meta;
    try { meta = await api.tutorial(app.tutorialId); } catch (err) {
      if (err.code === 'processing_failed') { error.hidden = false; rows.hidden = true; return; }
      showError(err); return;
    }
    if (meta.status === 'failed') {
      if (meta.source === 'manual') error.querySelector('.fail-text').textContent = "We couldn't turn this manual into steps. Try clearer photos or fewer pages.";
      error.hidden = false; rows.hidden = true; return;
    }
    const p = parse(meta.progress);
    const found = meta.status === 'ready' ? 10 : p?.stage === 'finding_steps' ? p.done : p ? p.total : 0;
    const total = p?.total || meta.step_count || 10;
    const watching = p?.stage === 'finding_steps' && !p.total; // real processing: one Muse Spark call, no count yet
    const doc = meta.source === 'manual' ? 'manual' : 'video'; // CR-006: a manual is read, not watched
    if (doc === 'manual') heading.textContent = 'Reading your manual…';
    const queued = p?.stage === 'queued'; // other uploads are using the processing slots
    sub.textContent = meta.status === 'ready' ? `Muse Spark found ${meta.step_count} steps in ${meta.title}.`
      : queued ? `${meta.title} · Other videos are being processed. Yours starts in a moment.`
      : p?.stage === 'outlining' ? `${meta.title} · ${total} steps found.`
        : watching ? `${meta.title} · Muse Spark is ${doc === 'manual' ? 'reading every page of the manual' : 'watching the whole video'}. This takes a minute or two.`
          : `${meta.title} · ${found} of ${total} steps found so far.`;
    reading.textContent = meta.status === 'ready' ? 'done' : p?.stage === 'outlining' ? 'outlining' : 'reading';
    clear(segs).append(...Array.from({ length: total }, (_, i) =>
      h('div', { class: `proc-seg ${i < found ? 'on' : i === found ? 'next' : ''}` })));
    const stage = (name, active, done) => h('div', { class: `proc-row ${done ? 'done' : active ? 'run' : 'wait'}` },
      h('span', { class: 'proc-icon', 'aria-hidden': 'true' }, done ? '✓' : ''), name);
    const outlining = p?.stage === 'outlining', ready = meta.status === 'ready';
    clear(rows).append(stage(doc === 'manual' ? 'Muse Spark is reading the manual, finding steps, and animating each one into the video…'
      : 'Muse Spark is watching the video and finding steps…', !outlining && !ready, outlining || ready),
      stage(`SAM 3.1 outlining the pieces ${doc === 'manual' ? "in each step's picture" : 'at the end of each step'}`, outlining, ready));
    const line = ready ? 'steps ready' : watching ? (doc === 'manual' ? 'reading the manual' : 'watching the video') : queued ? 'waiting for a free slot' : meta.progress;
    if (line && line !== lastLine) {
      lastLine = line;
      const cls = ready ? 'c-done' : outlining ? 'c-sam' : 'c-spark';
      log.append(h('div', { class: `log-line ${cls}` }, h('span', { class: 'dim' }, ready ? 'done' : '…'), h('span', {}, line.replace('_', ' '))));
    }
    if (ready) { timer = setTimeout(() => nav('ready'), 700); return; }
    timer = setTimeout(poll, 500);
  }
  poll();
  return () => { stopped = true; clearTimeout(timer); };
}
