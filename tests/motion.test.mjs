// node --test tests/*.test.mjs — UT-80–84 (motion helpers) and the pacing triggers motion feeds (UT-91, 95, 98, 104).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createDetector, changedFraction, greyscale } from '../web/js/motion.js';
import { initial, transition, S } from '../web/js/pacing.js';

const SET = { sample_w: 160, sample_h: 90, sample_ms: 100, pixel_delta: 25, still_fraction: 0.02, still_ms: 1500 };
const N = 160 * 90;
const frame = (v = 100) => new Uint8Array(N).fill(v);
// A frame where the first `share` of pixels are brightened by `delta`.
const changed = (share, delta = 60, base = 100) => { const f = frame(base); f.fill(base + delta, 0, Math.round(N * share)); return f; };

// Push frames every 100 ms from t0; returns the times "still" fired.
function feed(det, frames, t0 = 0) {
  const fired = [];
  frames.forEach((f, i) => { if (det.push(f, t0 + i * 100).still) fired.push(t0 + i * 100); });
  return fired;
}

test('greyscale and changedFraction', () => {
  assert.deepEqual([...greyscale(new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]))], [255, 0]);
  assert.equal(changedFraction(frame(), changed(0.1), 25), 0.1);
});

test('UT-80 identical frames for 1.6 s: one still event', () => {
  const fired = feed(createDetector(SET), Array.from({ length: 17 }, () => frame()));
  assert.deepEqual(fired, [1500]);
});

test('UT-81 frames changing 10% every 100 ms for 5 s: no still', () => {
  const frames = Array.from({ length: 50 }, (_, i) => (i % 2 ? changed(0.1) : frame()));
  assert.deepEqual(feed(createDetector(SET), frames), []);
});

test('UT-82 noise below the threshold', () => {
  // 1% of pixels flickering stays under 2%: still after 1.5 s.
  const flicker = Array.from({ length: 17 }, (_, i) => (i % 2 ? changed(0.01) : frame()));
  assert.deepEqual(feed(createDetector(SET), flicker), [1500]);
  // A change of 20/255 is ignored, 30/255 counted (pixel_delta 25).
  assert.equal(changedFraction(frame(), changed(0.5, 20), 25), 0);
  assert.equal(changedFraction(frame(), changed(0.5, 30), 25), 0.5);
});

test('UT-83 no repeat on an unchanged scene; motion then stillness fires again', () => {
  const det = createDetector(SET);
  assert.deepEqual(feed(det, Array.from({ length: 40 }, () => frame())), [1500], 'one event while unchanged');
  const moveThenStill = [changed(0.3), frame(), ...Array.from({ length: 20 }, () => frame())];
  assert.equal(feed(det, moveThenStill, 4000).length, 1, 'fires again after motion and stillness');
});

test('rearm after a check needs motion; rearm(false) retries at the next still moment', () => {
  const det = createDetector(SET);
  feed(det, Array.from({ length: 5 }, () => frame()));
  det.rearm(); // a check ran (e.g. Check me) while the scene was still
  assert.deepEqual(feed(det, Array.from({ length: 30 }, () => frame()), 500), [], 'no check on the unchanged scene');
  det.rearm(false); // the check failed: try again once still for still_ms
  assert.deepEqual(feed(det, Array.from({ length: 16 }, () => frame()), 4000), [5500]);
});

test('UT-84 settings from config: 2 s stillness', () => {
  const fired = feed(createDetector({ ...SET, still_ms: 2000 }), Array.from({ length: 25 }, () => frame()));
  assert.deepEqual(fired, [2000]);
});

// ---- pacing: what a "still" may trigger ----
const run = (ctx, ...events) => {
  let fx = [];
  for (const ev of events) { const r = transition(ctx, ev); ctx = r.ctx; fx = r.effects; }
  return { ctx, fx };
};

test('UT-91 still in WAITING sends one full check with trigger still', () => {
  const r = run(initial(3), { type: 'step_end' }, { type: 'still' });
  assert.equal(r.ctx.state, S.CHECKING);
  assert.deepEqual(r.fx, [{ type: 'check', id: 1, kind: 'full', trigger: 'still', confirm: false }]);
});

test('still never checks while playing, paused, checking or re-checking', () => {
  assert.equal(run(initial(3), { type: 'still' }).fx.length, 0, 'PLAYING');
  assert.equal(run(initial(3), { type: 'step_end' }, { type: 'pause' }, { type: 'still' }).fx.length, 0, 'PAUSED');
  assert.equal(run(initial(3), { type: 'step_end' }, { type: 'check_me' }, { type: 'still' }).fx.length, 0, 'UT-98 CHECKING');
  const confirming = run(initial(3), { type: 'step_end' }, { type: 'still' }, { type: 'verdict', id: 1, verdict: 'done' }).ctx;
  assert.equal(run(confirming, { type: 'still' }).fx.length, 0, 'CONFIRMING: the re-check comes from the timer');
  assert.equal(run(confirming, { type: 'recheck_due' }, { type: 'still' }).fx.length, 0, 're-check in flight');
  const asking = run(initial(3), { type: 'step_end' }, { type: 'still' }, { type: 'verdict', id: 1, verdict: 'unsure' }).ctx;
  assert.equal(run(asking, { type: 'still' }).fx.length, 0, 'ASKING waits for the answer');
});

test('UT-95 fixed during a mistake: still → done → re-check done → next step, no button', () => {
  let r = run(initial(3), { type: 'step_end' }, { type: 'still' }, { type: 'verdict', id: 1, verdict: 'wrong' });
  assert.equal(r.ctx.state, S.MISTAKE);
  r = run(r.ctx, { type: 'still' });
  assert.deepEqual(r.fx, [{ type: 'check', id: 2, kind: 'full', trigger: 'still', confirm: false }]);
  r = run(r.ctx, { type: 'verdict', id: 2, verdict: 'done' }, { type: 'recheck_due' }, { type: 'verdict', id: 3, verdict: 'done' });
  assert.equal(r.ctx.state, S.PLAYING);
  assert.equal(r.ctx.step, 2);
});

test('UT-104 check me checks at once and again after a check, with no motion between', () => {
  let r = run(initial(3), { type: 'step_end' }, { type: 'check_me' });
  assert.deepEqual(r.fx, [{ type: 'check', id: 1, kind: 'full', trigger: 'check_me', confirm: false }]);
  r = run(r.ctx, { type: 'verdict', id: 1, verdict: 'wrong' }, { type: 'check_me' });
  assert.deepEqual(r.fx, [{ type: 'check', id: 2, kind: 'full', trigger: 'check_me', confirm: false }]);
});
