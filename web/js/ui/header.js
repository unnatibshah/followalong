// Site header: a light one for the start/replay/summary pages, a dark one with the flow steps for S2–S9.
import { h, clear, icon } from './dom.js';
import { openCreate } from './create.js';
import { app } from '../state.js';

const LIVE_FLOW = [['ready', 'Ready'], ['review', 'Checks'], ['consent', 'Consent'], ['camera', 'Camera'], ['follow', 'Follow along'], ['summary', 'Summary']];
const REPLAY_FLOW = [['replay', 'Replay'], ['follow', 'Follow along'], ['summary', 'Summary']];

export function renderHeader(el, route, theme) {
  clear(el);
  document.getElementById('appnav')?.replaceChildren(); // the lesson screens are full-screen: no tabs
  el.className = `site-header ${theme}`;
  const logo = h('a', { href: '#/', class: 'logo' }, 'FollowAlong');
  if (theme === 'light') {
    // The app shell: tabs at the bottom on a phone, a rail on the left on a wide screen (CSS), same buttons
    const tab = (k, ic, label) => h('a', { href: `#/${k}`, class: ['tab', route === k && 'on'], 'aria-current': route === k ? 'page' : null },
      icon(ic), h('span', { class: 'tab-label' }, label));
    const tabs = h('nav', { class: 'tabbar', 'aria-label': 'App' },
      h('a', { href: '#/', class: 'rail-logo', 'aria-label': 'FollowAlong home' }, 'FA'),
      tab('', 'home', 'Home'), tab('library', 'library', 'Library'),
      h('button', { class: 'tab tab-new', type: 'button', onclick: openCreate, 'aria-label': 'New guide' },
        h('span', { class: 'tab-new-btn' }, icon('plus')), h('span', { class: 'tab-label' }, 'New')),
      tab('activity', 'clock', 'Activity'));
    el.append(h('div', { class: 'wrap header-row' }, logo,
      h('nav', { class: 'header-nav', 'aria-label': 'More' },
        h('a', { class: 'btn-text', href: '#/creator' }, 'For teams'),
        h('a', { href: '#/replay', class: 'btn btn-dark btn-sm' }, 'See it in action'))));
    // Outside the header: its blur makes it the box fixed children are placed in, and the tabs belong to the window
    document.getElementById('appnav')?.replaceChildren(tabs);
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
