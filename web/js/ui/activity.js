// Activity: every session you finished or ended, on this device (progress.js), with a weekly summary.
import { h, icon } from './dom.js';
import { app } from '../state.js';
import { nav } from '../router.js';
import { stepFrame } from '../thermostatscene.js';
import { runs } from '../progress.js';
import { empty } from './cards.js';

const WEEK = 7 * 86400000;
const mins = (s) => (s < 60 ? '<1 min' : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`);
function day(at) {
  const d = new Date(at), today = new Date();
  const diff = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  return diff === 0 ? 'Today' : diff === 1 ? 'Yesterday' : d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' });
}

export function render(root) {
  const all = runs();
  const week = all.filter((r) => Date.now() - r.at < WEEK);
  const stat = (n, label) => h('div', { class: 'stat' }, h('span', { class: 'stat-n' }, n), h('span', { class: 'stat-l' }, label));
  const finished = week.filter((r) => r.steps_done >= r.steps_total).length;

  const row = (r) => h('button', { class: 'act', type: 'button', onclick: () => { app.tutorialId = r.tid; app.mode = 'live'; nav('ready'); } },
    h('div', { class: 'act-media' }, stepFrame(r.tid, Math.max(1, Math.min(r.steps_done || 1, r.steps_total || 1)))),
    h('div', { class: 'act-body' },
      h('span', { class: 'act-title' }, r.title || 'A guide', r.mode === 'replay' && h('span', { class: 'tag' }, 'Replay'),
        r.times > 1 && h('span', { class: 'tag' }, `${r.times} sessions`)),
      h('span', { class: 'act-meta' }, `${r.steps_done} of ${r.steps_total} steps · ${mins(r.duration_s || 0)} · `
        + `${r.mistakes ? `${r.mistakes} mistake${r.mistakes === 1 ? '' : 's'} caught` : 'no mistakes'}`
        + `${r.hardest ? ` · hardest: step ${r.hardest}` : ''}`),
      h('span', { class: 'act-time' }, new Date(r.at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }))),
    h('span', { class: ['act-badge', r.steps_done >= r.steps_total ? 'done' : 'part'] }, r.steps_done >= r.steps_total ? icon('check') : `${r.steps_done}/${r.steps_total}`));

  // One row per guide (and per replay vs live): its latest session, with how many times you've done it
  const seen = new Map();
  for (const r of all) {
    const k = `${r.tid}|${r.mode}`;
    if (seen.has(k)) seen.get(k).times += 1; else seen.set(k, { ...r, times: 1 });
  }
  const groups = [];
  for (const r of seen.values()) {
    const d = day(r.at);
    if (!groups.length || groups[groups.length - 1].d !== d) groups.push({ d, items: [] });
    groups[groups.length - 1].items.push(r);
  }

  root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap app-page' },
    h('div', { class: 'page-head' }, h('div', {}, h('h1', { class: 'page-title' }, 'Activity'),
      h('p', { class: 'page-sub' }, 'Your sessions, saved on this device.'))),
    all.length ? h('div', { class: 'stats' }, stat(String(week.length), 'sessions this week'), stat(String(finished), 'guides finished'),
      stat(mins(week.reduce((a, r) => a + (r.duration_s || 0), 0)), 'hands-on time'),
      stat(String(week.reduce((a, r) => a + (r.mistakes || 0), 0)), 'mistakes caught')) : '',
    all.length ? groups.map((g) => h('section', { class: 'act-day' }, h('h2', { class: 'act-day-title' }, g.d), h('div', { class: 'act-list' }, g.items.map(row))))
      : empty('No sessions yet', 'When you finish or end a guide, it shows up here with your time, steps and the mistakes it caught.',
        h('a', { href: '#/replay', class: 'btn btn-accent' }, 'See it in action', icon('arrow'))),
    all.length ? h('p', { class: 'fine' }, 'Saved in this browser only. Delete my data on the ', h('a', { href: '#/privacy' }, 'privacy page'), ' clears it.') : '')));
  return null;
}
