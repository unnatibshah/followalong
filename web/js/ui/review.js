// S4 Review the checks (P1): keep, edit or remove checks, then PUT /api/tutorials/{id}/plan.
import { h, icon, clear, mmss } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { stepFrame } from '../thermostatscene.js';
import { nav } from '../router.js';
import { showError, toast } from '../overlays.js';
import { checkLabel } from './plan-text.js';

export async function render(root) {
  if (!app.tutorialId) { nav(''); return null; } // nothing chosen in this tab: back to S1
  let meta, plan;
  try { [meta, plan] = await Promise.all([api.tutorial(app.tutorialId), api.plan(app.tutorialId)]); } catch (err) { showError(err); return null; }
  plan = structuredClone(plan);
  let sel = Math.min(app.reviewStep || 1, plan.steps.length);
  let editing = null;
  const kept = new Set();
  const list = h('div', { class: 'review-list' });
  const detail = h('div', { class: 'review-detail' });
  const counts = h('span', { class: 'bar-counts' });

  const chip = (c, step) => h('span', { class: ['chip-s', c.kind, c.enabled === false && 'off'] },
    icon(c.kind === 'measured' ? 'ruler' : 'eye'), h('span', {}, checkLabel(c, step)));

  function paint() {
    clear(list).append(
      h('h1', { class: 'title-serif' }, 'Review the checks'),
      h('div', { class: 'sub' }, `${meta.title} · ${plan.steps.length} steps. Click a step to see its checks. Keep, edit or remove any check before you start.`),
      ...plan.steps.map((s) => h('button', { class: ['review-step', s.id === sel && 'sel'], 'aria-pressed': String(s.id === sel),
        onclick: () => { sel = s.id; editing = null; paint(); list.querySelector('.review-step.sel')?.focus(); } },
      h('span', { class: 'mono num' }, String(s.id)),
      h('span', { class: 'thumb' }, stepFrame(app.tutorialId, s.id, { variant: s.id < 3 ? 'before' : 'end', surface: 'studio', tags: false })),
      h('span', { class: 'col gap-s' }, h('span', { class: 'review-text' }, s.instruction), h('span', { class: 'chips' }, s.checks.map((c) => chip(c, s)))))));

    const s = plan.steps[sel - 1];
    clear(detail).append(
      h('div', { class: 'media-frame' }, stepFrame(app.tutorialId, s.id, { variant: s.id < 3 ? 'before' : 'end', surface: 'studio', outlines: true }),
        h('span', { class: 'pill tl' }, `Step ${s.id} of ${plan.steps.length} · ${mmss(s.start)}–${mmss(s.end)} ▶`)),
      h('div', { class: 'card-dark' },
        h('div', { class: 'quote' }, `“${s.instruction}”`),
        s.checks.map((c) => {
          const off = c.enabled === false;
          const text = editing === c.id
            ? h('form', { class: 'edit-row', onsubmit: (e) => { e.preventDefault(); c.text = e.target.elements.t.value.trim() || c.text; editing = null; toast('Check updated'); paint(); } },
              h('input', { name: 't', value: c.text, 'aria-label': 'Check text', class: 'input-dark' }),
              h('button', { class: 'btn btn-accent btn-xs' }, 'Save'))
            : h('span', { class: ['check-text', off && 'off'] }, c.text);
          const btn = (label, on, onclick) => h('button', { class: ['btn-chip', on && 'on'], 'aria-pressed': String(!!on), onclick }, label);
          return h('div', { class: 'check-row' },
            h('span', { class: `kind-tag ${c.kind}` }, c.kind === 'measured' ? '📏 measured' : '👁 judged'),
            h('div', { class: 'col gap-xs' }, text, c.needs_review && h('span', { class: 'needs-eyes' }, '⚠ needs your eyes')),
            h('div', { class: 'row-actions tight' },
              btn(kept.has(c.id) ? '✓ Kept' : 'Keep', kept.has(c.id), () => { kept.has(c.id) ? kept.delete(c.id) : kept.add(c.id); c.enabled = true; paint(); }),
              btn('Edit', editing === c.id, () => { editing = editing === c.id ? null : c.id; paint(); detail.querySelector('input')?.focus(); }),
              btn(off ? 'Removed ↺' : 'Remove', off, () => { c.enabled = off; kept.delete(c.id); paint(); })));
        }),
        h('div', { class: 'check-row' }, // hands_ok: checked while the learner holds it (letting go would undo the step)
          h('span', { class: 'kind-tag' }, '✋ hands'),
          h('span', { class: 'check-text' }, s.hands_ok ? 'Checked while you hold it: say "check me".' : 'Checked after you take your hands away.'),
          h('div', { class: 'row-actions tight' }, h('button', { class: ['btn-chip', s.hands_ok && 'on'], 'aria-pressed': String(!!s.hands_ok),
            onclick: () => { s.hands_ok = !s.hands_ok; paint(); } }, s.hands_ok ? '✓ Hands stay in view' : 'Hands stay in view')))),
      h('div', { class: 'info' }, 'ⓘ ', h('span', { class: 'c-measured' }, 'Measured'), ': decided by code from the outlines. ',
        h('span', { class: 'c-judged' }, 'Judged'), ': decided by Muse Spark.'));

    const all = plan.steps.flatMap((x) => x.checks).filter((c) => c.enabled !== false);
    counts.textContent = `${plan.steps.length} steps · ${all.filter((c) => c.kind === 'measured').length} measured checks · ${all.filter((c) => c.kind === 'judged').length} judged`;
  }

  async function save(start = true) {
    try {
      await api.savePlan(app.tutorialId, { ...plan, reviewed: true });
      toast('Checks saved');
      if (start) nav('consent');
    } catch (err) {
      if (err.code === 'forbidden' || err.code === 'invalid_plan') toast(err.message); else showError(err);
    }
  }

  root.append(h('div', { class: 'wrap-wide review' }, list, detail),
    h('div', { class: 'bottom-bar' },
      h('div', { class: 'row-actions' }, h('button', { class: 'btn-text dim', onclick: () => nav('ready') }, '← Back'), counts,
        h('button', { class: 'btn-text dim', onclick: async () => { await save(false); nav('checklist'); } }, 'Save and get the checklist')),
      h('button', { class: 'btn btn-accent btn-lg', onclick: () => save() }, 'Save and start →')));
  paint();
  return null;
}
