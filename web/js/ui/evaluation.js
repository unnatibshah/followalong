// S13 Evaluation (P2): GET /api/evaluation/latest: the newest eval/reports/*.json, or placeholders ("xx",
// `placeholder: true`) until `python -m server.evaluate` has written one.
import { h } from './dom.js';
import { api } from '../api.js';
import { showError } from '../overlays.js';

export async function render(root) {
  let e;
  try { e = await api.evaluation(); } catch (err) { showError(err); return null; }
  const v = (x) => (x === null || x === undefined ? '—' : String(x));
  const rows = [
    ['Step plans', `${v(e.step_plan_accuracy)} steps match our labels`],
    ['"Done" decisions', `${v(e.done_accuracy)} correct (checks: measured ${v(e.done_accuracy_measured)}, judged ${v(e.done_accuracy_judged)})`],
    ['Wrong advances', `${v(e.wrong_advances)}        Wrong corrections   ${v(e.wrong_corrections)}`],
    ['Mistakes caught', `${v(e.mistake_catch_rate)} at the right step`],
    ['Check latency', `median ${v(e.latency_ms_p50)} · 90th percentile ${v(e.latency_ms_p90)}`],
    e.cost_per_check_usd ? ['Cost per check', v(e.cost_per_check_usd)] : ['Cost per session', v(e.cost_per_session_usd)],
    ['Unsure rate', v(e.unsure_rate)],
  ];
  const tile = (n, t, d) => h('div', { class: 'tile' }, h('span', { class: 'mono muted small' }, n), h('span', { class: 'strong ink' }, t), h('span', { class: 'muted small' }, d));
  const limit = (n, t) => h('div', { class: 'limit' }, h('span', { class: 'mono muted small' }, n), h('span', {}, t));
  root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap col gap-l evaluation' },
    h('div', {}, h('h1', { class: 'section-title' }, 'How well it works'),
      h('div', { class: 'section-sub' }, e.placeholder ? `Latest evaluation, ${e.run_at}. The numbers below are placeholders; the real ones are always in the README.`
        : `Latest evaluation, ${e.run_at} · ${v(e.label_count)} labelled frames${e.note ? ` · ${e.note}` : ''}.`)),
    h('div', { class: 'table-card' }, rows.map(([k, v]) => h('div', { class: 'eval-row' }, h('span', {}, k), h('span', { class: 'mono ink' }, v))),
      h('div', { class: 'table-foot' }, e.placeholder ? 'Illustrative only. "xx" marks a number that comes from the evaluation run.'
        : `From ${e.config_hash ? `config ${e.config_hash}` : 'the evaluation run'}. Counts, not just percentages; the README has the caveats.`)),
    h('div', { class: 'tiles' }, tile('01', 'Tutorial video', 'Uploaded once, up to 3 minutes on this demo'),
      tile('02', 'Step plan', 'Steps, end frames and checks, written by Muse Spark'),
      tile('03', 'Live pacing', 'Pause and hand gate from SAM 3.1, voice commands from Muse Voice Transcribe'),
      tile('04', 'Checks', 'Measured in code from SAM outlines; judged by Muse Spark against the end frame')),
    h('div', { class: 'table-card pad col gap-s' }, h('span', { class: 'strong ink' }, 'Known limits'),
      limit('01', 'Needs a top-down camera. It can\'t measure stacking, so "on top of" checks are judged.'),
      limit('02', 'Small or clear pieces are outlined less reliably.'),
      limit('03', "Judged checks add about a second. When FollowAlong isn't sure, it asks you."),
      limit('04', 'Tested on thermostat wiring (no live power) and a Joy-Con stick swap only.'),
      limit('05', 'Live mains wiring (smart switches, 120/230 V) is out of scope. Low voltage only.')),
    h('div', {}, h('a', { href: '#/replay', class: 'btn btn-accent btn-lg' }, 'Watch a replay')))));
  return null;
}
