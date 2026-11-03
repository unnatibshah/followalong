// S11 Replay mode: pick a recorded session (GET /api/recordings), start a replay session, then S7–S10.
import { h } from './dom.js';
import { api } from '../api.js';
import { app, newSession } from '../state.js';
import { stopCamera } from '../camera.js';
import { nav } from '../router.js';
import { unlockSpeech } from '../speech.js';
import { showError } from '../overlays.js';

const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

export async function render(root) {
  stopCamera();
  let recs;
  try { recs = await api.recordings(); } catch (err) { showError(err); return null; }
  if (!recs.length) {
    root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap-mid col gap-l' },
      h('h1', { class: 'title-serif xl ink' }, 'No recorded sessions yet'),
      h('p', {}, 'Replays appear here once a live session is recorded and promoted (README: Recording a replay session).'),
      h('a', { href: '#/', class: 'btn btn-accent btn-lg' }, 'Start a guide'))));
    return null;
  }
  // Describe each recording from its own timeline, so the subtitle can't drift from the data.
  const timelines = await Promise.all(recs.map((r) => api.timeline(r.id).catch(() => [])));
  const subs = timelines.map((tl) => {
    const m = tl.filter((e) => e.type === 'mistake_shown' || (e.type === 'coach' && e.data?.status === 'mistake')
        || (e.type === 'check' && e.data?.verdict === 'wrong')).length,
      q = tl.filter((e) => e.type === 'ask_shown').length;
    return m || q ? [m && plural(m, 'correction'), q && plural(q, 'question')].filter(Boolean).join(' and ') : 'every step passes';
  });
  let pick = recs.findIndex((r) => r.id === app.recordingId);
  if (pick < 0) pick = Math.max(0, recs.findIndex((r) => r.id === 'figure-eight-pink-cord'));
  const art = h('div', { class: 'hero-frame still' });
  // The session's own practice footage (a frame from early in it), not the tutorial's picture
  const paintArt = () => art.replaceChildren(h('img', { class: 'frame-img', src: api.recordingFrameUrl(recs[pick].id, 20000), alt: recs[pick].title }),
    h('span', { class: 'pill tl' }, `▶ ${recs[pick]?.title || 'Recorded session'}`));
  paintArt();

  const opts = recs.map((r, i) => {
    const input = h('input', { type: 'radio', name: 'fa-rec', value: r.id, checked: i === pick, class: 'radio' });
    input.addEventListener('change', () => { pick = i; root.querySelectorAll('.rec-opt').forEach((l, j) => l.classList.toggle('on', j === i)); paintArt(); });
    return h('label', { class: ['rec-opt', i === pick && 'on'] }, input, h('span', { class: 'strong' }, r.title), h('span', { class: 'muted' }, subs[i]),
      r.credit && h('span', { class: 'muted fine' }, `Practice footage: ${r.credit}`));
  });

  async function play() {
    unlockSpeech(); // inside the tap: iOS only lets the coach speak once speech has started from one
    const r = recs[pick];
    try {
      const { session_id: sid } = await api.startSession({ tutorial_id: r.tutorial_id, mode: 'replay', recording_id: r.id });
      newSession(sid, 'replay');
      app.tutorialId = r.tutorial_id;
      app.recordingId = r.id;
      app.recordingTitle = r.title;
      nav('follow');
    } catch (err) { showError(err); }
  }

  root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap-mid split replay-pick' }, art,
    h('div', { class: 'col gap-l' },
      h('h1', { class: 'title-serif xl ink' }, '▶ Replay of a recorded session — no camera or API key needed'),
      h('fieldset', { class: 'col gap-s' }, h('legend', { class: 'strong' }, 'Choose a session:'), opts),
      h('button', { class: 'btn btn-accent btn-lg start', onclick: play, disabled: !recs.length }, 'Play the replay')))));
  return null;
}
