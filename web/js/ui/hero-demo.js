// Home hero: the real app, playing by itself, silently. The tutorial loops its step beside a recorded learner
// (practice footage from another video), the coach's lines appear as captions, and a tick lands when a step passes.
// It is a bundled replay drawn straight from its timeline, so what it shows is what the app does. Click to watch it all.
import { h, icon, reducedMotion } from './dom.js';
import { api } from '../api.js';

const REC = 'figure-eight-pink-cord';
const FRAME_MS = 250; // the grid make_replays.py writes frames on

export function heroDemo() {
  const tut = h('video', { class: 'frame-img', muted: true, playsInline: true, preload: 'auto', 'aria-hidden': 'true' });
  const you = h('img', { class: 'frame-img', alt: '', decoding: 'async' });
  const line = h('div', { class: 'hd-line' }, 'Show me your hands and what you’re working with.');
  const step = h('span', { class: 'hd-step' }, 'Getting ready');
  const tick = h('div', { class: 'hd-tick', 'aria-hidden': 'true' }, icon('check'));
  const youPane = h('div', { class: 'hd-pane you' }, you, h('span', { class: 'hd-tag' }, 'You'), tick);
  const credit = h('p', { class: 'hd-credit fine' });
  let paused = false;
  const pauseBtn = h('button', { class: 'hd-pause', type: 'button', 'aria-pressed': 'false', hidden: true }, 'Pause');
  pauseBtn.addEventListener('click', () => {
    paused = !paused;
    pauseBtn.textContent = paused ? 'Play' : 'Pause';
    pauseBtn.setAttribute('aria-pressed', String(paused));
    if (paused) tut.pause(); else tut.play().catch(() => {});
  });
  const el = h('div', { class: 'hero-demo' },
    h('a', { href: '#/replay', class: 'hd-link', 'aria-label': 'Watch a whole session: FollowAlong coaching a figure-eight knot' },
      h('div', { class: 'hd-panes' }, h('div', { class: 'hd-pane' }, tut, h('span', { class: 'hd-tag' }, 'Tutorial')), youPane),
      h('div', { class: 'hd-bar' }, step, line)),
    h('div', { class: 'hd-foot' }, pauseBtn, credit));
  run().catch(() => { el.classList.add('hd-off'); }); // no replay to show: the copy beside it still says it all
  return el;

  async function run() {
    const [tl, recs] = await Promise.all([api.timeline(REC), api.recordings().catch(() => [])]);
    const tid = tl[0].tutorial_id;
    const [plan, meta] = await Promise.all([api.plan(tid), api.tutorial(tid).catch(() => null)]);
    const rec = recs.find((r) => r.id === REC);
    credit.textContent = [rec?.credit && `Practice footage: ${rec.credit}`, meta?.creator && `tutorial by ${meta.creator}, ${(meta.licence || 'CC BY').split(' (')[0]}`]
      .filter(Boolean).join(' · ');
    tut.src = api.videoUrl(tid);
    const t0 = Date.parse(tl[0].at);
    const evs = tl.map((e) => ({ ...e, off: Date.parse(e.at) - t0 }));
    const end = evs[evs.length - 1].off + 1500;
    const first = Math.max(0, (evs.find((e) => e.type === 'step_play')?.off ?? 0) - 500);
    let clock = first, i = 0, range = null, shown = '';
    const total = plan.steps.length;

    const show = (ms) => {
      const src = api.recordingFrameUrl(REC, Math.floor(ms / FRAME_MS) * FRAME_MS, 640);
      if (src === shown) return;
      shown = src;
      const next = new Image(); // swapped in once loaded: no blank flashes
      next.onload = () => { if (shown === src) you.src = src; };
      next.src = src;
    };
    const win = () => { tick.classList.remove('on'); void tick.offsetWidth; tick.classList.add('on'); youPane.classList.add('win'); setTimeout(() => youPane.classList.remove('win'), 1200); };
    const apply = (e) => {
      const d = e.data || {};
      if (e.type === 'step_play') {
        const st = plan.steps[e.step_id - 1];
        range = [st.start, Math.max(st.start + 1, st.done_at || st.end)];
        tut.currentTime = range[0];
        tut.play().catch(() => {});
        step.textContent = `Step ${e.step_id} of ${total}`;
      } else if (e.type === 'coach' && d.say) {
        line.textContent = d.say;
        line.dataset.kind = d.status;
      } else if (e.type === 'check' && d.verdict === 'wrong') {
        line.textContent = 'Not quite. Have another look at the tutorial.';
        line.dataset.kind = 'mistake';
      } else if ((e.type === 'recheck' && d.verdict === 'done') || (e.type === 'ask_answer' && d.answer === 'yes') || (e.type === 'action' && d.name === 'next')) {
        win();
        line.textContent = e.step_id === total ? 'That was the last step. Great work.' : 'Nice, that’s it.';
        line.dataset.kind = 'done';
      }
    };

    while (i < evs.length && evs[i].off <= clock) apply(evs[i++]); // the setup lines, already said
    show(clock);
    if (reducedMotion()) return; // a still frame and the first line
    pauseBtn.hidden = false;
    const timer = setInterval(() => {
      if (!el.isConnected) { clearInterval(timer); tut.removeAttribute('src'); tut.load(); return; }
      if (document.hidden || paused) return;
      clock += FRAME_MS;
      if (clock > end) { clock = first; i = 0; while (i < evs.length && evs[i].off <= clock) apply(evs[i++]); } // and round again
      while (i < evs.length && evs[i].off <= clock) apply(evs[i++]);
      if (range && tut.currentTime >= range[1]) tut.currentTime = range[0];
      show(clock);
    }, FRAME_MS);
  }
}
