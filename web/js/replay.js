// Replay mode: drives pacing from a recorded timeline (GET /api/recordings/{id}/timeline), so the sequence of
// checks is the same every run. The server answers those checks from the recording's cached results.

// Advances aren't key moments: each click should land on the next question or mistake, then the end (S10).
const KEY_MOMENTS = new Set(['mistake_shown', 'ask_shown']);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** dispatch(event) runs a pacing event and resolves once its effects (e.g. the check request) are done.
 *  onEvent(event) fires as the clock reaches each recorded event (event.off = seconds from the start), for the frame;
 *  onTick(seconds) as the clock runs between events, so the recorded camera keeps moving.
 *  onQuestion(text) replays a spoken question; its answer is the server's fixture answer (no Meta call in replay). */
export function createReplay({ timeline, dispatch, onFinish, onEvent, onQuestion, onCoach, onTick }) {
  const t0 = Date.parse(timeline[0].at);
  const events = timeline.map((e) => ({ ...e, off: (Date.parse(e.at) - t0) / 1000 }));
  let clock = 0, paused = false, skipping = false, stopped = false;

  async function waitUntil(off) {
    let last = performance.now();
    while (clock < off && !stopped) {
      if (skipping) { clock = off; break; }
      await sleep(50);
      const now = performance.now();
      if (!paused) clock += (now - last) / 1000;
      last = now;
      onTick?.(clock); // the learner's camera moves between events (live-coach replays carry 4 frames a second)
    }
  }

  async function apply(e) {
    const d = e.data || {};
    switch (e.type) {
      case 'step_wait': return dispatch({ type: 'step_end' });
      case 'check': return dispatch({ type: d.trigger === 'still' ? 'still' : d.trigger === 'fixed' ? 'fixed' : 'check_me', kind: d.kind });
      case 'recheck': return dispatch({ type: 'recheck_due' });
      case 'ask_answer': return dispatch({ type: d.answer === 'yes' ? 'ask_yes' : 'ask_no' });
      case 'action': if (['again', 'next', 'skip'].includes(d.name)) return dispatch({ type: d.name }); return null;
      case 'question': return d.text ? onQuestion?.(d.text) : null; // only bundled recordings carry the text (we wrote it)
      case 'coach': return onCoach?.(d, e.step_id); // the live coach's look: what it saw and what it said
      case 'session_end': return onFinish();
      default: return null; // step_play, advance, mistake_shown, ask_shown follow from pacing itself
    }
  }

  async function run() {
    for (const e of events) {
      await waitUntil(e.off);
      if (stopped) return;
      onEvent?.(e);
      await apply(e);
      if (KEY_MOMENTS.has(e.type) || (e.type === 'coach' && e.data?.status === 'mistake')) skipping = false;
    }
  }

  return {
    start: () => { run(); },
    pause: () => { paused = true; },
    resume: () => { paused = false; },
    get paused() { return paused; },
    nextKeyMoment: () => { paused = false; skipping = true; },
    stop: () => { stopped = true; },
  };
}
