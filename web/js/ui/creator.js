// S12 Creator view (P2): stuck rates per step from GET /api/creator/{tutorial_id} (real session logs, or the sample
// fixture with `sample: true` until there are enough). Bars always carry their text.
import { h } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { nav } from '../router.js';
import { showError } from '../overlays.js';

const heat = (v) => (v === 0 ? 'h0' : v < 0.08 ? 'h1' : v < 0.13 ? 'h2' : v < 0.2 ? 'h3' : v < 0.3 ? 'h4' : 'h5');

export async function render(root) {
  const load = (id) => Promise.all([api.tutorial(id), api.plan(id), api.creator(id)]);
  let tid = app.tutorialId || 'breadboard-led'; // the guide you were just following, else a sample guide
  let meta, plan, view;
  try { [meta, plan, view] = await load(tid); } catch {
    tid = 'breadboard-led'; // that guide is gone (deleted, or a bad shared link): show the sample
    try { [meta, plan, view] = await load(tid); } catch (err) { showError(err); return null; }
  }
  const rows = view.steps, sessions = view.sessions;
  const source = view.sample ? ' · Sample data: these numbers are made up until real sessions are logged.' : ' from the session logs.';
  const pct = (v) => `${Math.round(v * 100)}%`;
  const edit = (step) => { app.tutorialId = tid; app.mode = 'live'; app.reviewStep = step; nav('review'); };
  const top = [...rows].sort((a, b) => b.stuck_rate - a.stuck_rate).filter((r) => r.stuck_rate > 0).slice(0, 3);

  root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap col gap-xl creator' },
    h('div', { class: 'row-between end' },
      h('div', {}, h('div', { class: 'eyebrow' }, 'For teams and creators'), h('h1', { class: 'section-title' }, 'Where learners get stuck'),
        h('p', { class: 'section-sub' }, 'Every session shows you which step people get stuck on and what they get wrong, so you know which instruction to fix.'),
        h('div', { class: 'section-sub' }, `${meta.title} · ${sessions} sessions${source}`)),
      h('button', { class: 'btn btn-white', onclick: () => edit(1) }, 'Review the checks')),
    h('div', { class: 'col gap-s' },
      h('div', { class: 'heat' }, rows.map((r) => {
        const st = plan.steps.find((x) => x.id === r.step_id);
        const col = h('div', { class: 'heat-col' }, h('div', { class: 'heat-num mono' }, String(r.step_id)),
          h('div', { class: `heat-bar mono ${heat(r.stuck_rate)}` }, pct(r.stuck_rate)));
        col.style.flexGrow = String(Math.min(30, Math.max(8, st ? st.end - st.start : 10)));
        return col;
      })),
      h('div', { class: 'muted small' }, 'Stuck = a "not quite" at that step, or more than 2 minutes there.')),
    !top.length && h('div', { class: 'note info' }, 'No one has got stuck on this guide yet. Steps where people need a second try will show up here.'),
    h('div', { class: 'stuck-list' }, top.map((r, i) => {
      const fill = h('div', { class: 'stuck-fill' });
      fill.style.width = pct(r.stuck_rate);
      return h('div', { class: 'stuck-row' },
        h('span', { class: 'rank' }, String(i + 1)),
        h('div', {}, h('div', { class: 'mono muted small' }, `Step ${r.step_id}`),
          h('div', { class: 'stuck-text' }, plan.steps.find((x) => x.id === r.step_id)?.instruction || '')),
        h('div', {}, h('div', { class: 'stuck-bar-row' }, h('div', { class: 'stuck-bar' }, fill), h('span', { class: 'stuck-rate' }, pct(r.stuck_rate))),
          h('div', { class: 'muted small' }, 'stuck rate')),
        h('div', {}, h('div', { class: 'stuck-mistake' }, r.top_mistakes[0] || '—'),
          h('div', { class: 'muted small' }, `${Math.round(r.stuck_rate * r.sessions)} of ${r.sessions} sessions`)),
        h('button', { class: 'btn btn-outline-light btn-sm', onclick: () => edit(r.step_id) }, 'Edit checks'));
    })))));
  return null;
}
