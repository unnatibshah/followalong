// Dev-only states menu (replaces the mockup's "states" switcher). Shown only when the server isn't hosted.
// Most items make the server return a real error or fallback for the next matching request (`?force=`).
import { h } from './dom.js';
import { api, forceNext } from '../api.js';
import { app } from '../state.js';
import { devSimulateCamera } from '../camera.js';
import { devBlockMic } from './camera-setup.js';
import { showOverlay, toast } from '../overlays.js';
import { nav, current } from '../router.js';

const CHECKS = 'POST /api/sessions/[^/]+/checks';
const ITEMS = [
  ['S1 · Tutorials fail to load', () => { forceNext('GET /api/tutorials$', 'bad_request'); nav(''); }],
  ['S1 · Video too long (next upload)', () => { forceNext('POST /api/tutorials$', 'too_long'); nav(''); }],
  ['S1 · Upload limit (next upload)', () => { forceNext('POST /api/tutorials$', 'limit_reached'); nav(''); }],
  ['S1–S3 · No API key (this tab)', () => { app.config.live_enabled = false; nav(current()); }],
  ['S2 · Couldn\'t find steps (during an upload)', () => forceNext('GET /api/tutorials/[^/]+$', 'processing_failed')],
  ['S5 · Live sessions used up', () => { forceNext('POST /api/sessions$', 'limit_reached'); nav('consent'); }],
  ['S5 · Server says consent required', () => { forceNext('POST /api/sessions$', 'consent_required'); nav('consent'); }],
  ['S5 · Camera blocked', () => { devSimulateCamera('NotAllowedError'); nav('consent'); }],
  ['S6 · Microphone blocked (next switch)', () => devBlockMic()],
  ['S7 · Demo budget reached (next check)', () => forceNext(CHECKS, 'cap_reached')],
  ['S7 · SAM 3.1 call failed (next check)', () => forceNext(CHECKS, 'sam_failed')],
  ['S7 · Hand in the way (next check)', () => forceNext(CHECKS, 'hand_blocked')],
  ['S9 · Muse Spark failed (next check)', () => forceNext(CHECKS, 'judge_failed')],
  ['S9 · SAM 3.1 can\'t find a piece (next check)', () => forceNext(CHECKS, 'object_missing')],
  ['Overlay · Cap reached', () => showOverlay('cap')],
  ['Overlay · Model error', () => showOverlay('model')],
  ['Overlay · Camera blocked', () => showOverlay('cam')],
  ['Overlay · Microphone blocked', () => showOverlay('mic')],
  ['Overlay · No API key', () => showOverlay('key')],
  ['Clear forced state', async () => { forceNext(null); app.config.live_enabled = (await api.config()).live_enabled; nav(current()); }],
];

export function initDevMenu() {
  const panel = h('div', { class: 'dev-panel', hidden: true, id: 'dev-panel' },
    h('div', { class: 'dev-head mono' }, 'dev states · local only · not part of the product'),
    ITEMS.map(([label, run]) => h('button', { class: 'dev-item', onclick: () => { panel.hidden = true; toggle.setAttribute('aria-expanded', 'false'); run(); toast(`dev: ${label}`); } }, label)));
  const toggle = h('button', { class: 'dev-toggle mono', 'aria-expanded': 'false', 'aria-controls': 'dev-panel',
    onclick: () => { panel.hidden = !panel.hidden; toggle.setAttribute('aria-expanded', String(!panel.hidden)); } }, 'dev');
  window.addEventListener('fa:escape', () => { panel.hidden = true; toggle.setAttribute('aria-expanded', 'false'); });
  document.body.append(h('div', { class: 'dev' }, panel, toggle));
}
