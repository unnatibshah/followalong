// S10 Completion and summary, from the SessionSummary returned by POST /api/sessions/{id}/end.
import { h } from './dom.js';
import { app } from '../state.js';
import { stepFrame } from '../thermostatscene.js';
import { stopCamera } from '../camera.js';
import { nav } from '../router.js';

const DONE = { 'install-thermostat': 'You installed the thermostat', 'light-switch': 'You installed the switch' };
const andList = (a) => (a.length < 2 ? a.join('') : `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}`);

export function render(root) {
  stopCamera();
  const s = app.summary;
  if (!s) { nav(''); return null; }
  const title = app.summaryTitle || 'tutorial';
  const mins = Math.max(1, Math.round(s.duration_s / 60));
  const caught = [...new Set(s.mistakes.map((m) => m.step_id))];
  const line = `${s.steps_done} steps · ${mins} min · ${caught.length} mistake${caught.length === 1 ? '' : 's'} caught`
    + (caught.length ? ` (step${caught.length > 1 ? 's' : ''} ${andList(caught.map(String))})` : '')
    + ` · ${s.rewinds} rewind${s.rewinds === 1 ? '' : 's'} by hand`;
  const steps = app.summaryPlan?.steps || [];
  const strip = steps.map((st) => {
    const mark = caught.includes(st.id);
    const seg = h(mark ? 'a' : 'div', { class: ['sum-seg', mark && 'mark', st.id <= s.steps_done && 'done'],
      href: mark ? '#/replay' : null, title: mark ? `Replay step ${st.id}` : `Step ${st.id}` }, String(st.id),
    mark && h('span', { class: 'sum-dot', 'aria-hidden': 'true' }));
    seg.style.flexGrow = String(Math.max(1, st.end - st.start)); // CSSOM, allowed by the CSP (no style attribute)
    return seg;
  });

  root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap col gap-xl summary' },
    h('div', { class: 'split' },
      h('div', { class: 'hero-frame' }, stepFrame(app.tutorialId, s.steps_total), h('span', { class: 'pill tl' }, 'Your finished work')),
      h('div', { class: 'col gap-m' },
        h('h1', { class: 'display md' }, s.steps_done === s.steps_total ? (DONE[app.tutorialId] || `You finished ${title}`) : `You did ${s.steps_done} of ${s.steps_total} steps`),
        h('div', { class: 'sum-line' }, line),
        h('div', { class: 'muted' }, s.hardest_step_id ? `Hardest step for you: ${s.hardest_step_id}` : 'No step held you up.'),
        h('div', { class: 'col gap-s start' },
          h('a', { href: '#/', class: 'btn btn-accent btn-lg', onclick: () => { app.sessionId = null; } }, 'Try another tutorial'),
          h('span', { class: 'mono fine' }, `Session ID ${app.sessionId} · quote it if you ask us to delete this session's log`)))),
    steps.length > 0 && h('div', { class: 'col gap-s' },
      h('div', { class: 'row-between muted' }, h('span', { class: 'strong ink' }, 'Your session'), h('span', {}, 'Click a marked step to replay that moment')),
      h('div', { class: 'sum-strip' }, strip)),
    h('a', { href: '#/creator', class: 'link' }, 'See where others get stuck →'))));
  return null;
}
