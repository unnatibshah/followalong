// node --test tests/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initial, transition, S } from '../web/js/pacing.js';

const run = (ctx, ...events) => {
  let fx = [];
  for (const ev of events) { const r = transition(ctx, ev); ctx = r.ctx; fx = r.effects; }
  return { ctx, fx };
};

test('play → wait → check → confirm → recheck → next step', () => {
  let { ctx, fx } = run(initial(3), { type: 'step_end' });
  assert.equal(ctx.state, S.WAITING);
  ({ ctx, fx } = run(ctx, { type: 'still' }));
  assert.equal(ctx.state, S.CHECKING);
  assert.deepEqual(fx, [{ type: 'check', id: 1, kind: 'full', trigger: 'still', confirm: false }]);
  ({ ctx, fx } = run(ctx, { type: 'verdict', id: 1, verdict: 'done' }));
  assert.equal(ctx.state, S.CONFIRMING);
  assert.deepEqual(fx, [{ type: 'schedule_recheck' }]);
  ({ ctx, fx } = run(ctx, { type: 'recheck_due' }));
  assert.equal(fx[0].kind, 'recheck');
  ({ ctx, fx } = run(ctx, { type: 'verdict', id: 2, verdict: 'done' }));
  assert.equal(ctx.state, S.PLAYING);
  assert.equal(ctx.step, 2);
  assert.deepEqual(fx, [{ type: 'play', step: 2, reason: 'advance' }]);
});

test('wrong → mistake → fixed → done; unsure → ask; hand → waiting', () => {
  let { ctx } = run(initial(3), { type: 'step_end' }, { type: 'check_me' }, { type: 'verdict', id: 1, verdict: 'wrong' });
  assert.equal(ctx.state, S.MISTAKE);
  ({ ctx } = run(ctx, { type: 'fixed' }));
  assert.equal(ctx.state, S.CHECKING);
  ({ ctx } = run(ctx, { type: 'verdict', id: 2, verdict: 'unsure' }));
  assert.equal(ctx.state, S.ASKING);
  let r = run(ctx, { type: 'ask_no' });
  assert.equal(r.ctx.state, S.PLAYING);
  assert.equal(r.ctx.step, 1);
  assert.equal(r.fx[0].reason, 'ask_no');
  r = run(ctx, { type: 'ask_yes' });
  assert.equal(r.ctx.step, 2);
  r = run(initial(2), { type: 'step_end' }, { type: 'still' }, { type: 'verdict', id: 1, verdict: 'waiting' });
  assert.equal(r.ctx.state, S.WAITING);
});

test('re-check that comes back wrong goes to MISTAKE, unsure to ASKING', () => {
  const base = run(initial(2), { type: 'step_end' }, { type: 'still' }, { type: 'verdict', id: 1, verdict: 'done' }, { type: 'recheck_due' }).ctx;
  assert.equal(run(base, { type: 'verdict', id: 2, verdict: 'wrong' }).ctx.state, S.MISTAKE);
  assert.equal(run(base, { type: 'verdict', id: 2, verdict: 'unsure' }).ctx.state, S.ASKING);
});

test('only one check in flight; stale verdicts are ignored', () => {
  let r = run(initial(3), { type: 'check_me' });
  assert.equal(r.fx.length, 1);
  r = run(r.ctx, { type: 'check_me' });
  assert.equal(r.fx.length, 0, 'second check while one is running');
  r = run(r.ctx, { type: 'next' }, { type: 'verdict', id: 1, verdict: 'wrong' });
  assert.equal(r.ctx.state, S.PLAYING);
  assert.equal(r.ctx.step, 2);
});

test('pause keeps the in-flight check and applies it on resume', () => {
  let r = run(initial(3), { type: 'step_end' }, { type: 'still' }, { type: 'pause' });
  assert.equal(r.ctx.state, S.PAUSED);
  r = run(r.ctx, { type: 'verdict', id: 1, verdict: 'wrong' });
  assert.equal(r.ctx.state, S.PAUSED);
  r = run(r.ctx, { type: 'resume' });
  assert.equal(r.ctx.state, S.MISTAKE);
});

test('cap error → CAPPED; other errors → WAITING; last step → DONE', () => {
  let r = run(initial(1), { type: 'check_me' }, { type: 'error', id: 1, code: 'cap_reached' });
  assert.equal(r.ctx.state, S.CAPPED);
  assert.deepEqual(r.fx, [{ type: 'capped', code: 'cap_reached' }]);
  r = run(initial(1), { type: 'check_me' }, { type: 'error', id: 1, code: 'check_in_progress' });
  assert.equal(r.ctx.state, S.WAITING);
  r = run(initial(1), { type: 'skip' });
  assert.equal(r.ctx.state, S.DONE);
  assert.deepEqual(r.fx, [{ type: 'done' }]);
  assert.equal(run(r.ctx, { type: 'again' }).ctx.state, S.DONE, 'nothing works after DONE');
});

test('jump plays the chosen step from any state; out-of-range jumps are ignored', () => {
  let r = run(initial(8), { type: 'step_end' }, { type: 'check_me' }, { type: 'jump', step: 5 });
  assert.equal(r.ctx.state, S.PLAYING);
  assert.equal(r.ctx.step, 5);
  assert.deepEqual(r.fx, [{ type: 'play', step: 5, reason: 'jump' }]);
  r = run(r.ctx, { type: 'verdict', id: 1, verdict: 'wrong' });
  assert.equal(r.ctx.state, S.PLAYING, 'the old check is stale after a jump');
  r = run(r.ctx, { type: 'jump', step: 9 });
  assert.equal(r.ctx.step, 5);
  assert.equal(r.fx.length, 0);
});

test('DEF-012: after a measured mistake the check is measured-only, then the re-check advances (FR-63)', () => {
  let r = run(initial(8), { type: 'step_end' }, { type: 'still' }, { type: 'verdict', id: 1, verdict: 'wrong', measuredFail: true });
  assert.equal(r.ctx.state, S.MISTAKE);
  r = run(r.ctx, { type: 'still' });
  assert.deepEqual(r.fx, [{ type: 'check', id: 2, kind: 'recheck', trigger: 'still', confirm: false }]);
  assert.equal(r.ctx.state, S.CHECKING);
  r = run(r.ctx, { type: 'verdict', id: 2, verdict: 'done' });
  assert.equal(r.ctx.state, S.CONFIRMING); // still two passing checks before the advance (FR-47)
  r = run(r.ctx, { type: 'recheck_due' });
  assert.deepEqual(r.fx, [{ type: 'check', id: 3, kind: 'recheck', trigger: 'recheck', confirm: true }]);
  r = run(r.ctx, { type: 'verdict', id: 3, verdict: 'done' });
  assert.equal(r.ctx.step, 2);
  // a judged mistake gets a full check; a replay sends the recorded kind
  r = run(initial(2), { type: 'step_end' }, { type: 'still' }, { type: 'verdict', id: 1, verdict: 'wrong', measuredFail: false }, { type: 'fixed' });
  assert.equal(r.fx[0].kind, 'full');
  r = run(initial(2), { type: 'step_end' }, { type: 'still' }, { type: 'verdict', id: 1, verdict: 'wrong', measuredFail: true }, { type: 'fixed', kind: 'full' });
  assert.equal(r.fx[0].kind, 'full');
});

test('safety gate: a gated step waits for the learner, once; checks are refused until then', () => {
  let { ctx, fx } = run(initial(3, { gates: [1] }), { type: 'step_end' });
  assert.equal(ctx.state, S.SAFETY);
  assert.deepEqual(fx, [{ type: 'safety', step: 1 }]);
  ({ ctx, fx } = run(ctx, { type: 'check_me' }));
  assert.equal(ctx.state, S.SAFETY);
  assert.deepEqual(fx, []);
  ({ ctx } = run(ctx, { type: 'safety_ok' }));
  assert.equal(ctx.state, S.WAITING);
  ({ ctx } = run(ctx, { type: 'again' }, { type: 'step_end' })); // replaying the same step doesn't ask again
  assert.equal(ctx.state, S.WAITING);
});

test('confirm-done step: a confirmed "done" asks the learner instead of advancing; override and yes advance', () => {
  let { ctx, fx } = run(initial(2, { confirmDone: [1] }), { type: 'step_end' }, { type: 'check_me' },
    { type: 'verdict', id: 1, verdict: 'done' }, { type: 'recheck_due' }, { type: 'verdict', id: 2, verdict: 'done' });
  assert.equal(ctx.state, S.ASKING);
  assert.equal(ctx.finalAsk, true);
  assert.deepEqual(fx, [{ type: 'final_ask' }]);
  ({ ctx } = run(ctx, { type: 'ask_yes' }));
  assert.equal(ctx.step, 2);
  ({ ctx, fx } = run(ctx, { type: 'step_end' }, { type: 'check_me' }, { type: 'verdict', id: 3, verdict: 'wrong' }, { type: 'override' }));
  assert.equal(ctx.state, S.DONE);
});

test('resume starts at the saved step, clamped to the lesson', () => {
  assert.equal(initial(5, { step: 4 }).step, 4);
  assert.equal(initial(5, { step: 9 }).step, 5);
  assert.equal(initial(5, { step: 0 }).step, 1);
});
