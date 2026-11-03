// Site header: a light one for the start/replay/summary pages, a dark one with the flow steps for S2–S9.
import { h, clear } from './dom.js';
import { app } from '../state.js';

const LIVE_FLOW = [['ready', 'Ready'], ['review', 'Checks'], ['consent', 'Consent'], ['camera', 'Camera'], ['follow', 'Follow along'], ['summary', 'Summary']];
const REPLAY_FLOW = [['replay', 'Replay'], ['follow', 'Follow along'], ['summary', 'Summary']];

export function renderHeader(el, route, theme) {
  clear(el);
  el.className = `site-header ${theme}`;
  const logo = h('a', { href: '#/', class: 'logo' }, 'FollowAlong');
  if (theme === 'light') {
    const nav = [['', 'Tutorials'], ['creator', 'For creators'], ['evaluation', 'Evaluation']].map(([k, label]) =>
      h('a', { href: `#/${k}`, class: ['nav-link', route === k && 'active'], 'aria-current': route === k ? 'page' : null }, label));
    el.append(h('div', { class: 'wrap header-row' }, logo,
      h('nav', { class: 'header-nav', 'aria-label': 'Main' }, nav,
        h('a', { class: 'btn-text', href: 'https://github.com/unnatibshah/followalong', target: '_blank', rel: 'noopener' }, 'GitHub'),
        h('a', { href: '#/replay', class: 'btn btn-dark btn-sm' }, 'Watch a replay (no camera)'))));
    return;
  }
  const flow = app.mode === 'replay' ? REPLAY_FLOW : LIVE_FLOW;
  const at = flow.findIndex(([k]) => k === route);
  const steps = flow.map(([k, label], i) => {
    const past = at > -1 && i < at;
    return h('a', { href: `#/${k}`, class: ['flow-step', k === route && 'active', past && 'past'], 'aria-current': k === route ? 'step' : null },
      h('span', { class: 'flow-num', 'aria-hidden': 'true' }, past ? '✓' : String(i + 1)), label);
  });
  const replay = app.mode === 'replay';
  el.append(h('div', { class: 'header-side' }, logo),
    h('nav', { class: 'flow-nav', 'aria-label': 'Steps' }, steps),
    h('div', { class: 'header-side end' },
      h('span', { class: ['mode-pill', replay ? 'replay' : 'live'] }, h('span', { class: 'dot', 'aria-hidden': 'true' }), replay ? 'Replay' : 'Live'),
      route === 'follow' && h('button', { class: 'btn btn-ghost-dark btn-sm', onclick: () => window.dispatchEvent(new Event('fa:end-session')) }, 'End session')));
}
