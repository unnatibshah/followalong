// #/annotate: a person verifies what the automatic evaluation assumed (local only; answers go to app/eval/human/).
// Approve / deny: labelling agents pre-filled every answer (eval/human/proposals/). Real mistakes: one frame at a time,
// A approves (with any edits), D denies, 1 2 3 0 change the state. Steps: the proposed fixes as current → proposed.
import { h } from './dom.js';
import { api } from '../api.js';

const post = (body) => fetch('/api/annotate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error('Not saved'))));
const img = (src, label) => h('figure', { class: 'ann-fig' }, h('img', { src, alt: label }), h('figcaption', {}, label));
const photo = (path) => `/api/annotate/image?path=${encodeURIComponent(path)}`;

export async function render(root) {
  let data;
  try { data = await fetch('/api/annotate/tasks').then((r) => { if (!r.ok) throw new Error(); return r.json(); }); } catch {
    root.append(h('div', { class: 'wrap-mid' }, h('h1', { class: 'title-serif xl ink' }, 'Annotation runs on a local FollowAlong only')));
    return null;
  }
  const done = { steps: new Set(data.done.steps), real: new Set(data.done.real) };
  const body = h('div', { class: 'ann-body' });
  const tabs = h('div', { class: 'ann-tabs' });
  let tab = 'real', keyHandler = null;
  const count = (k, total) => `${done[k].size} / ${total}`;

  function paintTabs() {
    tabs.replaceChildren(...[['real', 'Real mistakes: what does each frame show?', data.real.length],
      ['steps', 'Steps: are they right?', data.steps.reduce((n, t) => n + t.steps.length, 0)]].map(([k, label, total]) =>
      h('button', { class: ['btn', k === tab ? 'btn-accent' : 'btn-white'], onclick: () => { tab = k; paint(); } },
        `${label} · ${count(k, total)}`)));
  }
  function paint() { paintTabs(); keyHandler = null; ({ real, steps })[tab](); }

  const need = h('div', { class: 'note warn', hidden: true }, 'Pick which step it shows first (or press 0 if it shows no step).');
  const toastNeed = () => { need.hidden = false; setTimeout(() => { need.hidden = true; }, 2500); };

  const P = data.proposals || { real: {}, steps: {} };
  const STATE = { correct: 'Done right', in_progress: 'In progress', mistake: 'Mistake', irrelevant: 'Not relevant' };
  const conf = (c) => h('span', { class: `ann-conf ${c || 'none'}` }, c ? `${c} confidence` : 'no proposal');

  // ---- real mistakes: approve or deny the labelling agent's answer for each frame ----
  function real() {
    const queue = data.real.filter((f) => !done.real.has(f.key));
    let i = 0;
    const bulk = queue.filter((f) => P.real[f.key]?.state === 'irrelevant' && P.real[f.key]?.confidence === 'high');
    const bulkBtn = h('button', { class: 'btn btn-white btn-sm', hidden: !bulk.length, onclick: async () => {
      bulkBtn.disabled = true;
      for (const f of bulk) {
        await post({ kind: 'real', key: f.key, video: f.video, tutorial_id: f.tutorial_id, t: f.t, decision: 'approved', proposal: P.real[f.key],
          step_id: null, state: 'irrelevant', mistake: '', bulk: true });
        done.real.add(f.key);
      }
      paintTabs(); real();
    } }, `Approve all ${bulk.length} high-confidence "not relevant" frames at once`);
    const show = () => {
      const f = queue[i];
      if (!f) { body.replaceChildren(h('p', { class: 'body' }, 'All real frames reviewed. Thank you!')); return; }
      const pr = P.real[f.key] || {};
      let state = pr.state || null;
      const pick = h('select', { class: 'ann-select', 'aria-label': 'Which step does this frame show?' },
        h('option', { value: '' }, '— not any step of this tutorial —'),
        f.steps.map((st) => h('option', { value: st.id, selected: pr.step_id === st.id }, `${st.id}. ${st.instruction}`)));
      const ref = h('div', { class: 'ann-ref' });
      const what = h('input', { class: 'ask-input', value: pr.mistake || '', placeholder: 'What exactly is wrong? (for a mistake)' });
      const stateBtns = h('div', { class: 'ann-row' });
      const paintState = () => stateBtns.replaceChildren(...[['1', 'correct'], ['2', 'in_progress'], ['3', 'mistake'], ['0', 'irrelevant']].map(([k, v]) =>
        h('button', { class: ['btn', 'btn-sm', v === state ? 'btn-accent' : 'btn-white'], onclick: () => { state = v; paintState(); } }, h('kbd', { class: 'key' }, k), ` ${STATE[v]}`)));
      const paintRef = () => {
        const st = f.steps.find((x) => String(x.id) === pick.value);
        ref.replaceChildren(st ? img(api.frameUrl(f.tutorial_id, st.id), `Tutorial: step ${st.id} finished (for comparison)`) : h('p', { class: 'muted' }, 'No step'));
      };
      pick.addEventListener('change', () => { pick.blur(); paintRef(); });
      paintRef(); paintState();
      const save = async (decision) => {
        const final = { step_id: Number(pick.value) || null, state, mistake: state === 'mistake' ? what.value.trim() : '' };
        const changed = final.step_id !== (pr.step_id ?? null) || final.state !== pr.state || (final.mistake || '') !== (pr.mistake || '');
        await post({ kind: 'real', key: f.key, video: f.video, tutorial_id: f.tutorial_id, t: f.t, proposal: pr,
          decision: decision === 'denied' ? 'denied' : changed ? 'corrected' : 'approved', ...final });
        done.real.add(f.key); paintTabs(); i += 1; show();
      };
      keyHandler = (e) => {
        if (e.target === what) return;
        const k = e.key.toLowerCase();
        if (k === 'a') { e.preventDefault(); save('approve'); }
        else if (k === 'd') { e.preventDefault(); save('denied'); }
        else if ({ 1: 1, 2: 1, 3: 1, 0: 1 }[k]) { e.preventDefault(); state = { 1: 'correct', 2: 'in_progress', 3: 'mistake', 0: 'irrelevant' }[k]; paintState(); }
      };
      body.replaceChildren(
        h('div', { class: 'ann-row' }, h('span', { class: 'muted' }, `${f.creator} · “${f.title}” · ${Math.floor(f.t / 60)}:${String(f.t % 60).padStart(2, '0')} · ${queue.length - i} left`), bulkBtn),
        h('div', { class: 'ann-pair wide' }, img(photo(f.frame), 'Real video frame'), ref),
        h('div', { class: 'ann-coach' }, h('div', { class: 'ann-row' }, h('strong', {}, 'Claude proposes:'), conf(pr.confidence)),
          h('p', { class: 'strong' }, pr.state ? `${STATE[pr.state]}${pr.step_id ? ` · step ${pr.step_id}` : ''}${pr.mistake ? `: ${pr.mistake}` : ''}` : '(no proposal yet)'),
          pr.why ? h('p', { class: 'muted small' }, `Evidence: ${pr.why}`) : ''),
        h('div', { class: 'ann-row' }, h('strong', {}, 'Step'), pick), stateBtns, what,
        h('div', { class: 'ann-row' },
          h('button', { class: 'btn btn-accent ann-ans', onclick: () => save('approve') }, h('kbd', { class: 'key' }, 'A'), ' Approve (with any edits)'),
          h('button', { class: 'btn btn-white ann-ans', onclick: () => save('denied') }, h('kbd', { class: 'key' }, 'D'), ' Deny (proposal is wrong)'),
          h('button', { class: 'btn-text accent', onclick: () => { if (i > 0) { i -= 1; show(); } } }, '← Previous'),
          h('button', { class: 'btn-text accent', onclick: () => { i += 1; show(); } }, 'Skip →'),
          h('a', { class: 'btn-text accent', href: `${f.url}&t=${f.t}s`, target: '_blank', rel: 'noopener' }, 'Open the video here ↗')));
    };
    show();
  }

  // ---- steps: approve or deny the reviewing agent's proposed fixes ----
  function steps() {
    const pick = h('select', { class: 'ann-select', 'aria-label': 'Tutorial' },
      data.steps.map((t) => h('option', { value: t.tutorial_id }, `${t.title} (${t.steps.filter((s) => done.steps.has(s.key)).length}/${t.steps.length})`)));
    const list = h('div', { class: 'col gap-m' });
    const video = h('video', { class: 'ann-video', controls: true, preload: 'metadata' });
    let stopAt = null;
    video.addEventListener('timeupdate', () => { if (stopAt !== null && video.currentTime >= stopAt) { video.pause(); stopAt = null; } });
    const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
    const load = () => {
      const t = data.steps.find((x) => x.tutorial_id === pick.value);
      video.src = api.videoUrl(t.tutorial_id);
      list.replaceChildren(...t.steps.map((s) => stepCard(t, s)));
    };
    function stepCard(t, s) {
      const pr = P.steps[s.key] || {};
      const ed = pr.edits || {};
      const val = (k) => (k in ed ? ed[k] : s[k]);
      const num = (v) => h('input', { type: 'number', step: '0.1', min: 0, value: v, class: 'ann-num' });
      const start = num(val('start')), end = num(val('end'));
      const area = (v, rows = 2) => h('textarea', { class: 'ann-text', rows }, v || '');
      const instruction = area(val('instruction'), 1), say = area(val('say')), safety = area(val('safety'), 1);
      const pl = val('placements') || [];
      const placements = area(pl.join('\n'), Math.max(2, pl.length));
      const missing = h('input', { class: 'ask-input', value: pr.missing_after || '', placeholder: 'A step is missing after this one? (optional)' });
      const status = h('span', { class: 'muted' }, done.steps.has(s.key) ? '✓ reviewed' : '');
      const diff = Object.keys(ed).map((k) => h('div', { class: 'ann-diff' }, h('strong', {}, `${k}: `),
        h('span', { class: 'ann-old' }, Array.isArray(s[k]) ? s[k].join(' · ') : String(s[k] ?? '')), ' → ',
        h('span', { class: 'ann-new' }, Array.isArray(ed[k]) ? ed[k].join(' · ') : String(ed[k]))));
      const save = async (decision) => {
        const final = { start: Number(start.value), end: Number(end.value), instruction: instruction.value.trim(), say: say.value.trim(),
          safety: safety.value.trim(), placements: placements.value.split('\n').map((x) => x.trim()).filter(Boolean) };
        await post({ kind: 'steps', key: s.key, tutorial_id: t.tutorial_id, step_id: s.id, proposal: pr, decision,
          verdict: decision === 'denied' ? 'keep_current' : pr.verdict === 'not_a_step' ? 'not_a_step' : 'ok', final,
          missing_after: decision === 'denied' ? '' : missing.value.trim() });
        done.steps.add(s.key); status.textContent = `✓ ${decision}`; paintTabs();
      };
      return h('div', { class: 'ann-step card-light' },
        h('div', { class: 'ann-row' }, h('strong', {}, `Step ${s.id}`), conf(pr.confidence),
          h('span', { class: `ann-verdict ${pr.verdict || ''}` }, { ok: 'Claude: looks right', fix: 'Claude: proposes fixes', not_a_step: 'Claude: not a real step' }[pr.verdict] || ''),
          h('button', { class: 'btn btn-white btn-sm', onclick: () => { video.currentTime = Number(start.value); stopAt = Number(end.value); video.play(); } },
            `▶ Play ${fmt(Number(start.value))}–${fmt(Number(end.value))}`), status),
        pr.why ? h('p', { class: 'muted small' }, `Evidence: ${pr.why}`) : '',
        diff.length ? h('div', { class: 'ann-coach' }, diff) : '',
        pr.missing_after ? h('p', { class: 'strong' }, `Missing step after this: ${pr.missing_after}`) : '',
        h('details', {}, h('summary', {}, 'Edit before approving'),
          h('div', { class: 'col gap-s' },
            h('div', { class: 'ann-row' }, h('label', {}, 'Start (s) ', start), h('label', {}, 'End (s) ', end)),
            h('label', { class: 'col' }, 'Instruction', instruction), h('label', { class: 'col' }, 'Exact directions', say),
            h('label', { class: 'col' }, 'Placements (one per line)', placements), h('label', { class: 'col' }, 'Safety line', safety), missing)),
        h('div', { class: 'ann-row' },
          h('button', { class: 'btn btn-accent btn-sm', onclick: () => save('approved') }, '✓ Approve'),
          h('button', { class: 'btn btn-white btn-sm', onclick: () => save('denied') }, '✗ Deny (keep the current step)')));
    }
    pick.addEventListener('change', load);
    body.replaceChildren(h('div', { class: 'ann-row' }, h('strong', {}, 'Tutorial'), pick),
      h('p', { class: 'muted' }, 'Each step shows what Claude found in the video and the change it proposes (current → proposed). Play the clip if unsure, then Approve or Deny.'),
      h('div', { class: 'ann-steps' }, h('div', { class: 'ann-sticky' }, video), list));
    load();
  }

  const onKey = (e) => { if (keyHandler && !e.metaKey && !e.ctrlKey && !e.altKey) keyHandler(e); };
  window.addEventListener('keydown', onKey);
  root.append(h('div', { class: 'wrap-wide col gap-m ann' },
    h('h1', { class: 'title-serif lg ink' }, 'Help check FollowAlong'),
    h('p', { class: 'body' }, 'Claude has pre-filled every answer. You approve (A) or deny (D); fix anything before approving and it counts as your correction. Your decisions become the ground truth FollowAlong is measured and tuned against.'),
    tabs, need, body));
  paint();
  return () => window.removeEventListener('keydown', onKey);
}
