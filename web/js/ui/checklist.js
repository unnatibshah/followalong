// Checklist: the step plan as a page to tick off without a camera, or print / save as PDF (the browser's own dialog).
// Free after the one-time processing: no camera, no model calls. Live checking is the "Start following along" path.
import { h } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { stepFrame } from '../thermostatscene.js';
import { nav } from '../router.js';
import { showError } from '../overlays.js';

export async function render(root) {
  if (!app.tutorialId) { nav(''); return null; }
  let meta, plan;
  let details = {};
  try { [meta, plan, details] = await Promise.all([api.tutorial(app.tutorialId), api.plan(app.tutorialId), api.details(app.tutorialId).catch(() => ({}))]); } catch (err) { showError(err); return null; }
  const tick = (label) => h('label', { class: 'tick' }, h('input', { type: 'checkbox' }), h('span', {}, label));

  root.append(h('div', { class: 'wrap-mid checklist' },
    h('div', { class: 'row-between no-print' },
      h('button', { class: 'btn-text', onclick: () => nav('ready') }, '← Back'),
      h('button', { class: 'btn btn-accent', onclick: () => window.print() }, 'Download (print or save as PDF)')),
    h('h1', { class: 'title-serif lg ink' }, `${meta.title}: checklist`),
    h('p', { class: 'muted' }, `${plan.steps.length} steps`, meta.creator ? ` · from the video by ${meta.creator}` : '',
      meta.source_url ? [' (', h('a', { href: meta.source_url }, `“${meta.source_title || meta.title}”`), `, ${meta.licence})`] : '',
      ' · made by FollowAlong with Muse Spark'),
    // A paper checklist has nobody correcting it, so say when no person has reviewed the checks yet (S4 sets it)
    !plan.reviewed && h('div', { class: 'note warn' }, 'Not reviewed by a person yet. Compare each step with the video, or use ',
      h('a', { href: '#/review' }, 'Review the checks'), ' first.'),
    plan.needs.length > 0 && h('section', { class: 'cl-needs' }, h('h2', {}, "You'll need"), plan.needs.map(tick)),
    plan.steps.map((s) => h('section', { class: 'cl-step' },
      h('div', { class: 'cl-frame' }, stepFrame(app.tutorialId, s.id)),
      h('div', { class: 'col gap-s' },
        h('h2', {}, `${s.id}. ${details[s.id]?.title || s.instruction}`),
        details[s.id]?.safety && h('p', { class: 'strong' }, `⚠️ ${details[s.id].safety}`),
        details[s.id] && h('p', {}, details[s.id].say),
        details[s.id]?.placements.length > 0 && h('ul', { class: 'detail-list' }, details[s.id].placements.map((x) => h('li', {}, x))),
        h('p', {}, h('span', { class: 'strong' }, 'Done looks like: '), s.done_looks_like),
        s.hands_ok && h('p', { class: 'muted' }, '✋ Keep holding it while you check: letting go undoes this step.'),
        s.checks.filter((c) => c.enabled !== false).map((c) => tick(c.text)),
        s.common_mistakes.length > 0 && h('p', { class: 'muted' }, h('span', { class: 'strong' }, 'Watch out: '), s.common_mistakes.join('; ')))))));
  return null;
}
