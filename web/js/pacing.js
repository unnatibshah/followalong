// Pacing state machine (technical design §4.4). Pure: transition(ctx, event) -> { ctx, effects }.
// The follow screen performs the effects (play video, send a check, schedule a re-check, finish).

export const S = Object.freeze({
  PLAYING: 'PLAYING', WAITING: 'WAITING', CHECKING: 'CHECKING', CONFIRMING: 'CONFIRMING',
  MISTAKE: 'MISTAKE', ASKING: 'ASKING', PAUSED: 'PAUSED', DONE: 'DONE', CAPPED: 'CAPPED', SAFETY: 'SAFETY',
});

const CAP_CODES = new Set(['cap_reached', 'limit_reached']);

// opts.gates: steps whose safety line must be confirmed before the learner's turn (power off at the breaker…);
// opts.confirmDone: steps that never advance on the camera's word alone (the learner has the final say);
// opts.step: where to start (resume). Safety tiers: server/safety.py.
export function initial(total, { gates = [], confirmDone = [], step = 1 } = {}) {
  return { state: S.PLAYING, step: Math.min(Math.max(1, step), total), total, prev: null, checkId: 0, inFlight: null,
    pending: null, lastVerdict: null, measuredMistake: false, gates, confirmDone, safe: [], finalAsk: false };
}

// Effects: {type:'play', step, reason} · {type:'check', id, kind, trigger, confirm} · {type:'schedule_recheck'}
//          {type:'done'} · {type:'capped', code} · {type:'safety', step} · {type:'final_ask'}
export function transition(ctx, ev) {
  const fx = [];
  const c = { ...ctx };

  const play = (step, reason) => {
    c.state = S.PLAYING; c.step = step; c.inFlight = null; c.lastVerdict = null; c.measuredMistake = false; c.finalAsk = false;
    fx.push({ type: 'play', step, reason });
  };
  const advance = (reason = 'advance') => {
    if (c.step >= c.total) { c.state = S.DONE; c.inFlight = null; fx.push({ type: 'done' }); }
    else play(c.step + 1, reason);
  };
  // confirm: the re-check ~1 s after a `done` (FR-47); its `done` advances. Any other check is a first check.
  const check = (kind, trigger, confirm = false) => {
    c.checkId += 1;
    c.inFlight = { id: c.checkId, kind, confirm };
    c.state = confirm ? S.CONFIRMING : S.CHECKING;
    fx.push({ type: 'check', id: c.checkId, kind, trigger, confirm });
  };
  // The check after a mistake: a measured fail is fixed when the measured checks pass (CR-004), so it is
  // measured-only like the re-check (Muse Spark isn't asked again; FR-63). A judged mistake gets a full check.
  // Replay passes the recorded `kind`, so a replay sends the same requests as the recorded session.
  const firstCheck = (ev) => check(ev.kind || (c.state === S.MISTAKE && c.measuredMistake ? 'recheck' : 'full'), ev.type);
  const verdict = (v, confirm, measuredFail) => {
    c.inFlight = null; c.lastVerdict = v;
    if (v === 'done') {
      if (confirm && c.confirmDone.includes(c.step)) { c.state = S.ASKING; c.finalAsk = true; fx.push({ type: 'final_ask' }); }
      else if (confirm) advance();
      else { c.state = S.CONFIRMING; fx.push({ type: 'schedule_recheck' }); }
    } else if (v === 'wrong') { c.state = S.MISTAKE; c.measuredMistake = !!measuredFail; }
    else if (v === 'unsure') c.state = S.ASKING;
    else c.state = S.WAITING; // waiting: hand in the way, or a fallback
  };

  if (c.state === S.DONE) return { ctx: c, effects: fx };

  if (c.state === S.PAUSED) {
    if (ev.type === 'resume') {
      c.state = c.prev; c.prev = null;
      if (c.pending) { const p = c.pending; c.pending = null; return transition(c, p); }
    } else if ((ev.type === 'verdict' || ev.type === 'error') && c.inFlight && ev.id === c.inFlight.id) {
      c.pending = ev; // keep the in-flight check's answer; apply it on resume
    }
    return { ctx: c, effects: fx };
  }

  switch (ev.type) {
    case 'step_end':
      if (c.state === S.PLAYING) {
        const gated = c.gates.includes(c.step) && !c.safe.includes(c.step);
        c.state = gated ? S.SAFETY : S.WAITING;
        if (gated) fx.push({ type: 'safety', step: c.step });
      }
      break;
    case 'safety_ok':
      if (c.state === S.SAFETY) { c.safe = [...c.safe, c.step]; c.state = S.WAITING; }
      break;
    case 'override': // the learner says the coach is wrong: after a measured or judged "wrong", or at a question
      if (c.state === S.MISTAKE || c.state === S.ASKING) advance('override');
      break;
    case 'still':
      if ((c.state === S.WAITING || c.state === S.MISTAKE) && !c.inFlight) firstCheck(ev);
      break;
    case 'check_me':
    case 'fixed':
      if (!c.inFlight && c.state !== S.CAPPED && c.state !== S.SAFETY) firstCheck(ev);
      break;
    case 'recheck_due':
      if (c.state === S.CONFIRMING && !c.inFlight) check('recheck', 'recheck', true);
      break;
    case 'verdict':
      if (c.inFlight && ev.id === c.inFlight.id) verdict(ev.verdict, c.inFlight.confirm, ev.measuredFail);
      break;
    case 'error':
      if (c.inFlight && ev.id === c.inFlight.id) {
        c.inFlight = null;
        if (CAP_CODES.has(ev.code)) { c.state = S.CAPPED; fx.push({ type: 'capped', code: ev.code }); }
        else c.state = S.WAITING;
      }
      break;
    case 'ask_yes':
      if (c.state === S.ASKING) advance();
      break;
    case 'ask_no':
      if (c.state === S.ASKING) play(c.step, 'ask_no');
      break;
    case 'again':
      play(c.step, 'again');
      break;
    case 'jump': // a progress segment: play that step from its start
      if (ev.step >= 1 && ev.step <= c.total) play(ev.step, 'jump');
      break;
    case 'next':
      advance('advance');
      break;
    case 'skip':
      advance('skip');
      break;
    case 'pause':
      c.prev = c.state; c.state = S.PAUSED;
      break;
  }
  return { ctx: c, effects: fx };
}
