// #/ops: how the service is doing (GET /api/metrics): model latency and errors in the last hour, the shared API rate
// limit, spend, browser errors and the product funnel over 7 days. Aggregates only; refreshes every 10 s.
import { h, clear } from './dom.js';
import { api } from '../api.js';

const tile = (value, label) => h('div', { class: 'ops-tile' }, h('b', {}, value ?? '–'), h('span', {}, label));
const pct = (x) => (x == null ? '–' : `${Math.round(x * 100)}%`);

export async function render(root) {
  const body = h('div', { class: 'col gap-l' });
  root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap col gap-l ops' },
    h('h1', { class: 'title-serif lg ink' }, 'Operations'), body)));
  let timer = 0;
  async function load() {
    let m;
    try { m = await api.metrics(); } catch (err) { clear(body).append(h('p', { class: 'note warn' }, err.message)); return; }
    const f = m.funnel;
    clear(body).append(
      h('h2', {}, 'Learners, last 7 days'),
      h('div', { class: 'ops-tiles' }, tile(f.sessions, 'live sessions'), tile(pct(f.completion_rate), 'finished the lesson'),
        tile(f.median_minutes != null ? `${f.median_minutes} min` : null, 'median session'), tile(f.steps_done, 'steps done'),
        tile(f.mistakes_shown, 'mistakes caught'), tile(f.overrides, '"No, it\'s right" (coach overruled)'),
        tile(f.safety_confirmed, 'safety steps confirmed'), tile(f.resumed, 'lessons resumed')),
      h('h2', {}, 'Meta API, right now'),
      h('div', { class: 'ops-tiles' }, tile(pct(m.rate_limit.load), `of ${m.rate_limit.rpm} requests/min used`),
        tile(m.rate_limit.shed_looks, 'coach looks skipped (busy)'), tile(`$${m.spend.day_usd}`, `spent today (cap $${m.spend.caps.day})`),
        tile(`$${m.spend.total_usd}`, `spent in total (cap $${m.spend.caps.total})`)),
      h('h2', {}, 'Model calls, last hour'),
      h('table', {}, h('thead', {}, h('tr', {}, ['Model', 'Calls', 'Errors', 'p50', 'p95', 'Cost'].map((t) => h('th', {}, t)))),
        h('tbody', {}, Object.entries(m.models).map(([name, r]) => h('tr', {}, h('td', {}, name), h('td', { class: 'num' }, r.calls),
          h('td', { class: 'num' }, r.errors), h('td', { class: 'num' }, r.p50_ms != null ? `${r.p50_ms} ms` : '–'),
          h('td', { class: 'num' }, r.p95_ms != null ? `${r.p95_ms} ms` : '–'), h('td', { class: 'num' }, `$${r.cost_usd.toFixed(3)}`))))),
      h('h2', {}, 'Browser errors'),
      m.client_errors.length ? h('ul', {}, m.client_errors.map((e) => h('li', { class: 'small' }, `${e.at} · ${e.message} · ${e.where}`)))
        : h('p', { class: 'dim' }, 'None.'));
    timer = setTimeout(load, 10000);
  }
  await load();
  return () => clearTimeout(timer);
}
