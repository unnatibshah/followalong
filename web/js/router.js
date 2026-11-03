// Hash router: #/<route>. Light pages and the dark follow-along flow get different headers.
import { renderHeader } from './ui/header.js';
import { closeAll } from './overlays.js';
import { app } from './state.js';
import { h } from './ui/dom.js';
import * as start from './ui/start.js';
import * as understanding from './ui/understanding.js';
import * as ready from './ui/ready.js';
import * as review from './ui/review.js';
import * as checklist from './ui/checklist.js';
import * as consent from './ui/consent.js';
import * as cameraSetup from './ui/camera-setup.js';
import * as follow from './ui/follow.js';
import * as replayPick from './ui/replay-pick.js';
import * as creator from './ui/creator.js';
import * as evaluation from './ui/evaluation.js';
import * as legal from './ui/legal.js';
import * as annotate from './ui/annotate.js';
import * as ops from './ui/ops.js';
import * as library from './ui/library.js';
import * as activity from './ui/activity.js';

const ROUTES = {
  '': [start, 'light'], understanding: [understanding, 'dark'], ready: [ready, 'dark'], review: [review, 'dark'], checklist: [checklist, 'light'],
  consent: [consent, 'dark'], camera: [cameraSetup, 'dark'], follow: [follow, 'dark'],
  replay: [replayPick, 'light'], creator: [creator, 'light'], evaluation: [evaluation, 'light'],
  privacy: [legal.privacy, 'light'], terms: [legal.terms, 'light'], annotate: [annotate, 'light'], ops: [ops, 'light'],
  library: [library, 'light'], activity: [activity, 'light'],
  404: [{ render: (root) => { root.append(h('div', { class: 'wrap col gap-l page-404' }, h('h1', { class: 'title-serif xl ink' }, 'That page isn’t here'),
    h('p', { class: 'muted' }, 'The link may be old, or the guide was deleted.'),
    h('div', { class: 'row-actions' }, h('a', { class: 'btn btn-accent', href: '#/' }, 'Go home'), h('a', { class: 'btn btn-white', href: '#/library' }, 'All guides')))); return null; } }, 'light'],
};

let cleanup = null;
export const current = () => { const r = location.hash.replace(/^#\/?/, '').split('?')[0]; return r in ROUTES ? r : r ? '404' : ''; };
/** A shared link: #/ready?t=<tutorial id> opens that tutorial. */
const sharedTutorial = () => new URLSearchParams(location.hash.split('?')[1] || '').get('t');

export function nav(route) {
  const hash = `#/${route}`;
  if (location.hash === hash || (route === '' && !location.hash)) render();
  else location.hash = hash;
}

let seq = 0;
async function render() {
  const token = ++seq;
  if (cleanup) { try { cleanup(); } catch { /* screen already gone */ } cleanup = null; }
  closeAll();
  const route = current();
  const shared = sharedTutorial();
  if (shared) { app.tutorialId = shared; app.mode = 'live'; }
  const [screen, theme] = ROUTES[route];
  document.body.dataset.theme = theme;
  document.body.dataset.route = route || 'start';
  const name = { 404: 'Not found', follow: 'Lesson', camera: 'Camera', replay: 'Demo', creator: 'For teams', library: 'Library', activity: 'Activity', ready: 'Guide', consent: 'Before you start', checklist: 'Checklist', review: 'Review the checks', understanding: 'Building your guide', evaluation: 'Evaluation' }[route];
  document.title = name ? `${name} · FollowAlong` : 'FollowAlong · instructions that see your work';
  renderHeader(document.getElementById('header'), route, theme);
  // A fresh box per render: a slow screen that finishes after the next navigation draws into a detached box.
  const box = document.createElement('div');
  box.className = 'screen screen-in'; // a short fade-up between screens (none with reduced motion)
  const main = document.getElementById('main');
  main.replaceChildren(box);
  window.scrollTo(0, 0);
  const done = (await screen.render(box)) || null;
  if (token !== seq) { if (done) done(); return; }
  cleanup = done;
  main.focus({ preventScroll: true });
}

export function startRouter() {
  window.addEventListener('hashchange', render);
  render();
}
