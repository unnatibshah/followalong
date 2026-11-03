// S7 Following along · S8 Not quite · S9 Does yours look like this? — one screen driven by the pacing machine.
import { h, icon, clear, mmss, reducedMotion } from './dom.js';
import { api } from '../api.js';
import { app, nextCheckNo } from '../state.js';
import { initial, transition, S } from '../pacing.js';
import { createPlayer } from '../player.js';
import { createReplay } from '../replay.js';
import { grabFrame, hasCamera, cameraView, frameQuality } from '../camera.js';
import { createMotion } from '../motion.js';
import { thermostatScene, outlineLayer, drawsStandIn } from '../thermostatscene.js';
import { setShortcuts, openKeyboardPanel } from '../keyboard.js';
import { showError, showOverlay, toast } from '../overlays.js';
import { nav } from '../router.js';
import { checkLabel, formatValue, newObjects } from './plan-text.js';
import { createVoice, askMic } from '../voice.js';
import { whenQuiet, speak, hush, onSpeech, setVoiceLang } from '../speech.js';
import { saveProgress, clearProgress } from '../progress.js';

const ORB = {
  playing: 'Watch', hands: 'Your turn', blocked: "Can't see it all", checking: 'Checking',
  recheck: 'Double-checking', done: 'Step done', not_quite: 'Not quite', unsure: 'Does yours look like this?',
  paused: 'Paused', cap: 'Live checks paused', samFail: 'Waiting', safety: 'Safety first',
};
// Status line, exactly per the UX doc's S7 table.
const STATUS = {
  playing: (n) => `Watch step ${n}`, hands: () => "Your turn. I'm watching.",
  blocked: () => "I can't see part of the work. Move your hand a little.",
  checking: (n, spark) => `Checking step ${n}… (SAM 3.1 outlining${spark ? ', Muse Spark comparing' : ''})`,
  recheck: () => 'Looks done. Double-checking…', done: (n) => `✓ Step ${n} done`,
  not_quite: () => "Not quite. Fix it and I'll look again.", unsure: () => 'Waiting for your answer',
  paused: () => 'Paused', cap: () => 'Live checks are paused (demo budget reached). Watch a replay instead.',
  samFail: () => "Couldn't check, waiting. I'll try again when you're still.",
  safety: () => "Do the safety step, then tell me it's safe.",
};
const LIT = { hands: ['sam'], blocked: ['sam'], samFail: ['sam'], recheck: ['sam'] };
const badgeOf = (model) => (model.startsWith('sam') ? 'sam' : model.startsWith('muse-spark') ? 'spark' : null);
const ADVANCE_FLASH_MS = 1200;

export async function render(root) {
  if (!app.sessionId) { nav(app.mode === 'replay' ? 'replay' : ''); return null; }
  const replayMode = app.mode === 'replay';
  let meta, plan, timeline, details, phrases;
  const lang = replayMode ? 'en' : app.lang;
  setVoiceLang(lang);
  try {
    [meta, plan, timeline, details, phrases] = await Promise.all([api.tutorial(app.tutorialId), api.plan(app.tutorialId),
      replayMode ? api.timeline(app.recordingId) : null, api.details(app.tutorialId, lang).catch(() => ({})),
      api.phrases(lang).catch(() => null)]);
  } catch (err) { showError(err); return null; }
  // The coach's fixed lines, in the learner's language (config/phrases.json, translated by the server)
  const T = (key, vars = {}) => String((phrases || {})[key] || PHRASES_EN[key] || key)
    .replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ''));

  const sid = app.sessionId;
  const steps = plan.steps;
  const stepOf = (n) => steps[n - 1];
  const detailOf = (n) => details?.[String(n)] || null;   // exact directions (server/details.py), may be missing
  const directions = (n) => detailOf(n)?.say || stepOf(n)?.instruction || '';
  // The step's name in everyday words (details `title`), else the plan's instruction
  const titleOf = (n) => detailOf(n)?.title || stepOf(n)?.instruction || '';
  const enabledChecks = (n) => stepOf(n).checks.filter((c) => c.enabled !== false);
  // A full check of this step calls Muse Spark (judged checks the camera can see; always-ask ones never go out).
  const usesSpark = (n) => enabledChecks(n).some((c) => c.kind === 'judged') && !enabledChecks(n).some((c) => c.always_ask);
  // Safety tiers (server/safety.py): a `gate` step waits for the learner to confirm it's safe; a `gate` or `danger`
  // step never advances on the camera's word alone. Replays play what was recorded, so they have neither.
  const hazard = (n) => (replayMode ? '' : detailOf(n)?.hazard || '');
  const ids = steps.map((_, i) => i + 1);
  const resumeAt = replayMode ? 1 : (app.resumeStep || 1);
  app.resumeStep = null;
  let ctx = initial(steps.length, { gates: ids.filter((n) => hazard(n) === 'gate'),
    confirmDone: ids.filter((n) => ['gate', 'danger'].includes(hazard(n))), step: resumeAt });
  let last = null;          // the latest CheckResult for the current step
  let flash = null;         // step number just finished, shown for a moment before the next one plays
  let flashLast = null;     // that step's final CheckResult: `last` is already cleared for the next step
  let drawerOpen = false;
  let safetyShownFor = 0;
  let clipPaused = false, clipPlay = false;
  let finished = false, completed = false;
  const timers = new Set();
  const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };
  const log = (type, step_id, data = {}) => api.event(sid, { type, step_id, at: new Date().toISOString(), data }).catch(() => {});

  // ---- static structure ----
  const el = {};
  const pill = (cls, ...kids) => h('div', { class: `pill ${cls}` }, ...kids);
  const player = createPlayer({
    src: api.videoUrl(app.tutorialId), standIn: drawsStandIn(app.tutorialId),
    onEnd: () => { if (!replayMode) dispatch({ type: 'step_end' }); },
    onTime: (t, frac) => { el.playTime.textContent = mmss(t); el.playFill.style.width = `${Math.round(frac * 100)}%`; },
    onSoundBlocked: () => { el.soundBtn.hidden = false; },
  });
  // The learner tapped Start (consent) or Play (replay) to get here, so the tutorial may play with its sound
  player.sound(true);

  el.orbIcon = h('span', { class: 'orb-icon', 'aria-hidden': 'true' });
  el.orbWord = h('span', { class: 'orb-word' });
  el.orb = h('div', { class: 'orb' }, el.orbIcon, el.orbWord);

  el.tutLabel = pill('tl');
  el.frozen = pill('tr mono', icon('pause'), h('span'));
  el.playTime = h('span', {}, '0:00');
  el.playFill = h('div', { class: 'bar-fill' });
  el.playBar = h('div', { class: 'play-bar' }, icon('play'), el.playTime, h('div', { class: 'bar' }, el.playFill), h('span', {}, mmss(meta.duration_s)));
  // The clip runs unless paused; with reduced motion it starts as a still frame until Play (NFR-61).
  const clipRunning = () => !clipPaused && (!reducedMotion() || clipPlay);
  el.clipBtn = h('button', { class: 'pill tr clip-btn', onclick: (e) => {
    e.stopPropagation();
    if (clipRunning()) { clipPaused = true; player.pause(); } else { clipPaused = false; clipPlay = true; player.resume(); }
    paint();
  } });
  el.loopBar = h('div', { class: 'loop-bar' }, h('div', { class: 'loop-fill' }));
  el.clipStill = h('div', { class: 'panel-cover' }, h('button', { class: 'round-play', 'aria-label': 'Play the clip',
    onclick: (e) => { e.stopPropagation(); clipPlay = true; clipPaused = false; player.resume(); paint(); } }, icon('play')));
  el.pausedCover = h('div', { class: 'panel-cover' }, h('div', { class: 'round-play' }, icon('play')));
  el.soundBtn = h('button', { class: 'sound-chip', type: 'button', hidden: true,
    onclick: (e) => { e.stopPropagation(); el.soundBtn.hidden = true; player.sound(true); } }, '\ud83d\udd08 Tap for sound');
  el.tut = h('div', { class: 'panel tut', onclick: () => togglePause('button'), title: 'Pause or resume (P)' },
    player.el, el.soundBtn, el.tutLabel, el.frozen, el.playBar, el.clipBtn, el.loopBar, el.clipStill, el.pausedCover);

  el.liveMedia = h('div', { class: 'live-media' });
  el.liveOverlay = h('div', { class: 'live-overlay' });
  el.liveLabel = pill('tl', h('span', { class: 'rec-dot', 'aria-hidden': 'true' }), h('span'));
  el.passPill = pill('br pass');
  el.meterFill = h('span', { class: 'meter-fill' });
  el.meter = pill('bl motion-meter', h('span', { class: 'meter-label' }, 'motion'),
    h('span', { class: 'meter-track', 'aria-hidden': 'true' }, el.meterFill, h('span', { class: 'meter-mark' })));
  el.meter.hidden = true;
  el.framing = pill('tr framing');
  el.framing.hidden = true;
  el.live = h('div', { class: 'panel live' }, el.liveMedia, el.liveOverlay, el.liveLabel, el.passPill, el.meter, el.framing);
  const camVideo = hasCamera() && !replayMode ? cameraView() : null;
  if (camVideo) el.liveMedia.append(camVideo);
  // Replay: the recorded preview frame at the replay clock (GET /api/recordings/{id}/frames/{ms}).
  const recFrame = replayMode ? h('img', { class: 'frame-img', alt: 'Recorded view of the learner\'s desk' }) : null;
  const showRecorded = (ms) => { const src = api.recordingFrameUrl(app.recordingId, ms); if (recFrame.getAttribute('src') !== src) recFrame.src = src; };
  // On the 250 ms grid make_replays.py writes a live-coach replay's frames on, so each frame is fetched once
  const showRecordedAt = (sec) => showRecorded(Math.floor((sec * 1000) / 250) * 250);
  if (recFrame) { el.liveMedia.append(recFrame); showRecorded(0); }
  // Motion (TD §4.1) on the camera; in replay on the recorded frames, for the meter only (checks come from the
  // timeline). The stand-in scene has no motion: Check me (C) still works.
  const mo = app.config.motion;
  // The live coach watches continuously (live, a real camera, real checks); it replaces the "check when still" trigger
  const watcherOn = !replayMode && !!camVideo && app.config.live_checks?.source === 'sam';
  let lastMove = 0;
  const motion = camVideo || recFrame ? createMotion({
    source: camVideo || recFrame, settings: mo,
    onStill: () => { if (!watcherOn && !replayMode && !finished && [S.WAITING, S.MISTAKE].includes(ctx.state) && !ctx.inFlight) dispatch({ type: 'still' }); },
    onLevel: (fraction, moving) => {
      if (moving) lastMove = Date.now();
      el.meter.hidden = false;
      // The bar is full at 4× the still threshold; the mark shows the threshold itself.
      el.meterFill.style.width = `${Math.min(100, (fraction / (mo.still_fraction * 4)) * 100)}%`;
      el.meter.classList.toggle('moving', moving);
      el.meter.title = `${(fraction * 100).toFixed(1)}% of pixels changed (still under ${mo.still_fraction * 100}%)`;
    },
  }) : null;
  let sceneKey = '';

  el.segs = h('div', { class: 'segs' }, steps.map((st, i) => h('button', { class: 'seg', title: `Step ${i + 1}: ${st.instruction}`,
    'aria-label': `Go to step ${i + 1}`, onclick: () => jump(i + 1) })));
  el.statusDot = h('span', { class: 'status-dot', 'aria-hidden': 'true' });
  el.statusText = h('span');
  el.status = h('div', { class: 'status-line', role: 'status', 'aria-live': 'polite' }, el.statusDot, el.statusText);

  el.capText = h('span');
  el.hintTag = h('span', { class: 'kind-tag' });
  el.caption = h('div', { class: 'caption', 'aria-live': 'polite' }, h('span', { class: 'cc', 'aria-hidden': 'true' }, 'CC'), el.capText, el.hintTag);
  // Voice captions (always on): the learner's words in grey, the answer in white.
  el.heard = h('span', { class: 'heard' });
  el.reply = h('span', { class: 'reply' });
  el.voiceCap = h('div', { class: 'caption voice-caption', 'aria-live': 'polite', hidden: true },
    h('span', { class: 'cc', 'aria-hidden': 'true' }, 'CC'), h('span', { class: 'voice-lines' }, el.heard, el.reply));
  el.mistakeActs = h('div', { class: 'row-actions' },
    h('button', { class: 'btn btn-outline-dark', onclick: () => act('again', 'button') }, 'Show me again'),
    h('button', { class: 'btn btn-light', onclick: () => act('fixed', 'button') }, 'I fixed it'),
    h('button', { class: 'btn btn-outline-dark', onclick: () => act('skip', 'button') }, 'Skip this step'),
    h('button', { class: 'btn btn-outline-dark', title: 'The check got it wrong: yours is right', onclick: () => override('button') }, "No, it's right"));
  el.safetyText = h('span', { class: 'ask-q' });
  el.safety = h('div', { class: 'ask-panel safety-panel', role: 'alertdialog', 'aria-label': 'Safety step' },
    h('div', { class: 'ask-text' }, h('span', { class: 'safety-tag' }, '⚠️ Safety first'), el.safetyText,
      h('span', { class: 'ask-why' }, "I can't see electricity, heat or pressure through a camera, so only you can confirm this.")),
    h('div', { class: 'row-actions' },
      h('button', { class: 'btn btn-light btn-lg', onclick: () => confirmSafe('button') }, "It's safe, I've done that"),
      h('button', { class: 'btn btn-outline-dark btn-lg', onclick: () => act('skip', 'button') }, 'Skip this step')));
  el.askReason = h('span', { class: 'ask-reason' });
  el.yes = h('button', { class: 'btn btn-light btn-lg', onclick: () => answer('yes', 'button') });
  el.no = h('button', { class: 'btn btn-outline-dark btn-lg', onclick: () => answer('no', 'button') });
  el.ask = h('div', { class: 'ask-panel' },
    h('div', { class: 'ask-text' }, el.askQ = h('span', { class: 'ask-q' }),
      el.askWhy = h('span', { class: 'ask-why' }), el.askReason),
    h('div', { class: 'row-actions' }, el.yes, el.no));

  // Typed questions (live, voice off): the same /questions call as a spoken one; the captions show both.
  el.askInput = h('input', { type: 'text', class: 'ask-input', maxlength: 500, autocomplete: 'off',
    placeholder: 'Ask a question about this step', 'aria-label': 'Ask a question about this step' });
  el.askForm = h('form', { class: 'ask-form', hidden: true },
    el.askInput, h('button', { type: 'submit', class: 'btn btn-outline-dark btn-sm' }, 'Ask'));
  el.askForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = el.askInput.value.trim();
    if (!text) return;
    el.askInput.value = '';
    askQuestionAloud(text, false);
  });
  el.railCount = h('span', { class: 'mono dim' });
  el.railList = h('div', { class: 'rail-list' });
  el.rail = h('aside', { class: 'rail', 'aria-label': 'Steps' }, h('div', { class: 'rail-head' }, h('span', {}, 'Steps'), el.railCount), el.railList);

  const badge = (key, name) => h('div', { class: `badge ${key}` }, h('span', { class: 'dot', 'aria-hidden': 'true' }), name, h('span', { class: 'lat mono' }));
  el.badges = { spark: badge('spark', 'Muse Spark'), sam: badge('sam', 'SAM 3.1'), voice: badge('voice', 'Voice') };
  el.listening = h('span', { class: 'listening' }, '🎙 Listening');
  el.watchNote = h('div', { class: 'watch-note', 'aria-live': 'off', hidden: true }, '👁 Watching…');
  el.micBtn = h('button', { class: 'btn btn-outline-dark btn-sm mic-btn', type: 'button' });
  el.muteBtn = h('button', { class: 'btn btn-outline-dark btn-sm', type: 'button', hidden: !replayMode });
  const ctl = (label, key, onclick, primary) => {
    const b = h('button', { class: ['btn', primary ? 'btn-accent' : 'btn-outline-dark', 'ctl'], onclick, title: `${label} (${key})` },
      h('span', { class: 'ctl-label' }, label), h('kbd', { class: 'key' }, key));
    return b;
  };
  el.controls = {
    check: ctl('Check me', 'C', () => act('check_me', 'button'), true),
    again: ctl('Again', 'A', () => act('again', 'button')),
    next: ctl('Next', 'N', () => act('next', 'button')),
    skip: ctl('Skip', 'S', () => act('skip', 'button')),
    pause: ctl('Pause', 'P', () => togglePause('button')),
  };
  el.talkBtn = h('button', { class: 'btn btn-outline-dark ctl talk-btn', title: 'Hold to talk (hold V)', hidden: true },
    h('span', { class: 'ctl-label' }, 'Hold to talk'), h('kbd', { class: 'key' }, 'V'));
  // Pointer capture: a press lasts until the pointer is released, even if it drifts off the button (a pointerleave
  // used to end the clip early, and a clip under 0.3 s is never sent).
  el.talkBtn.addEventListener('pointerdown', (e) => { e.preventDefault(); el.talkBtn.setPointerCapture?.(e.pointerId); voiceCtl?.press(); });
  for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) el.talkBtn.addEventListener(ev, () => voiceCtl?.release());
  el.drawerBtn = h('button', { class: 'btn-round', title: 'How it decided (D)', 'aria-label': 'How it decided', 'aria-expanded': 'false', onclick: () => toggleDrawer() }, 'D');
  el.drawer = h('aside', { class: 'drawer', 'aria-label': 'How it decided', hidden: true });

  const head = h('div', { class: 'follow-head' });
  const title = h('div', { class: 'follow-title' });
  // Exact directions for the step on screen: where each piece goes (row, column, terminal, polarity)
  const detailBox = h('details', { class: 'step-detail' });
  let detailFor = 0;
  function paintDetail(n) {
    if (n === detailFor) return;
    detailFor = n;
    const d = detailOf(n);
    clear(detailBox);
    if (!d) { detailBox.hidden = true; return; }
    detailBox.hidden = false;
    detailBox.append(h('summary', {}, d.safety ? h('span', { class: 'detail-safety' }, `⚠️ ${d.safety} `) : '',
      h('span', { class: 'detail-say' }, d.say), h('span', { class: 'detail-more' }, ' exact placements')),
      d.placements.length ? h('ul', { class: 'detail-list' }, d.placements.map((x) => h('li', {}, x))) : '',
      d.watch_for.length ? h('p', { class: 'detail-watch' }, `Watch for: ${d.watch_for.join(' ')}`) : '',
      d.check_yourself ? h('p', { class: 'detail-check' }, `Check yourself: ${d.check_yourself}`) : '');
  }
  let banner = null, bannerPause = null;
  if (replayMode) {
    bannerPause = h('button', { class: 'btn btn-outline-dark btn-xs', onclick: () => togglePause('button') }, '⏸ Pause');
    banner = h('div', { class: 'replay-banner' },
      h('span', { class: 'banner-title' }, '▶ Replay of a recorded session', h('span', { class: 'dim' }, ` · ${app.recordingTitle || ''}`)),
      h('div', { class: 'row-actions' }, bannerPause,
        h('button', { class: 'btn btn-accent btn-xs', onclick: () => { if (ctx.state === S.PAUSED) togglePause('button'); replay.nextKeyMoment(); } }, 'Next key moment →')));
  }

  const followEl = h('div', { class: 'follow' }, banner,
    h('div', { class: 'follow-inner' }, head, title, detailBox,
      h('div', { class: 'follow-grid' },
        h('div', { class: 'follow-col' },
          h('div', { class: 'orb-row' }, el.orb),
          h('div', { class: 'panels' }, el.tut, el.live),
          h('div', { class: 'under' }, el.segs, el.status),
          el.watchNote,
          h('div', { class: 'talk' }, el.caption, el.voiceCap, el.safety, el.mistakeActs, el.ask, el.askForm)),
        el.rail),
      h('div', { class: 'follow-bar' },
        h('div', { class: 'badges' }, el.micBtn, el.muteBtn, el.badges.spark, el.badges.sam, el.badges.voice, el.listening,
          h('button', { class: 'btn-text accent', onclick: openKeyboardPanel }, 'Keyboard')),
        h('div', { class: 'controls' }, el.talkBtn, Object.values(el.controls), el.drawerBtn))),
    el.drawer);
  root.append(followEl);

  // ---- view ----
  function orbKey() {
    if (flash) return 'done';
    switch (ctx.state) {
      case S.PLAYING: return 'playing';
      case S.WAITING: return last?.hand_blocked ? 'blocked' : last?.fallback === 'sam_failed' ? 'samFail' : 'hands';
      case S.CHECKING: return 'checking';
      case S.CONFIRMING: return 'recheck';
      case S.MISTAKE: return 'not_quite';
      case S.ASKING: return 'unsure';
      case S.PAUSED: return 'paused';
      case S.CAPPED: return 'cap';
      case S.SAFETY: return 'safety';
      default: return 'done';
    }
  }

  function orbIcon(key) {
    if (key === 'playing') return icon('play');
    if (key === 'paused' || key === 'cap') return icon('pause');
    if (key === 'samFail') return h('span', { class: 'breathe' });
    if (key === 'hands' || key === 'blocked') return icon('hand');
    if (key === 'checking' || key === 'recheck') return icon('spin', 'spin');
    if (key === 'done') return icon('check');
    if (key === 'not_quite') return icon('x');
    if (key === 'safety') return h('span', { class: 'q' }, '!');
    return h('span', { class: 'q' }, '?');
  }

  function chipStatus(c) {
    if (ctx.inFlight && (ctx.inFlight.kind !== 'recheck' || c.kind === 'measured')) return 'running';
    if (!last) return 'pending';
    const r = last.results.find((x) => x.check_id === c.id);
    if (!r) return c.always_ask && last.verdict === 'unsure' ? 'unsure' : 'skip';
    if (last.verdict === 'unsure' && r.kind === 'judged' && r.pass) return 'unsure';
    return r.pass ? 'pass' : 'fail';
  }

  function failedResults() { return last ? last.results.filter((r) => !r.pass) : []; }

  function badObjects(n) {
    const step = stepOf(n);
    const names = new Set();
    for (const r of failedResults()) {
      const c = step.checks.find((x) => x.id === r.check_id);
      const involved = [c?.a, c?.b].filter(Boolean);
      const fresh = involved.filter((name) => newObjects(steps, n).has(name));
      (fresh.length ? fresh : involved).forEach((name) => names.add(name));
    }
    return names;
  }

  // the size of the frames grabFrame sends (the camera's shape, longest side 1280): outlines are in those pixels
  function frameSize() {
    const w = camVideo?.videoWidth, hgt = camVideo?.videoHeight;
    if (!w || !hgt) return [1280, 720];
    const k = 1280 / Math.max(w, hgt);
    return [Math.round(w * k), Math.round(hgt * k)];
  }

  function paintLive(key, n) {
    if (!camVideo && !recFrame) {
      const variant = ['playing', 'hands', 'blocked', 'samFail'].includes(key) && !last?.objects?.length ? 'before'
        : last?.verdict === 'wrong' ? 'wrong' : 'end';
      const sk = `${variant}-${!!last?.hand_blocked}`;
      if (sk !== sceneKey) { sceneKey = sk; clear(el.liveMedia).append(thermostatScene({ tutorial: app.tutorialId, variant, surface: 'desk', hand: !!last?.hand_blocked })); }
    }
    clear(el.liveOverlay);
    if (last?.objects?.length && ctx.state !== S.PLAYING) el.liveOverlay.append(outlineLayer(last.objects, badObjects(n), frameSize()));
  }

  function paint() {
    const n = flash || ctx.step;
    const step = stepOf(ctx.step);
    const key = orbKey();
    const checks = enabledChecks(ctx.step);
    followEl.dataset.orb = key;
    head.textContent = key === 'not_quite' ? `Step ${n} · Not quite` : key === 'unsure' ? `Step ${n} · Does yours look like this?`
      : `${meta.title} · Step ${n} of ${steps.length}${stepOf(n).manual_page ? ` · Manual p. ${stepOf(n).manual_page}` : ''}`;
    title.textContent = titleOf(n);
    paintDetail(n);

    el.orb.dataset.orb = key;
    clear(el.orbIcon).append(orbIcon(key));
    el.orbWord.textContent = key === 'done' ? `Step ${n} done` : ORB[key];
    const sparkNow = usesSpark(ctx.step) && ctx.inFlight?.kind === 'full'; // re-checks and measured-only steps don't call it
    // A hands_ok step is checked while you hold it: say "check me" (or press C) instead of letting go
    el.statusText.textContent = stepOf(n).hands_ok && (key === 'hands' || key === 'not_quite')
      ? 'Keep holding it. Say "check me" or press C.' : STATUS[key](n, sparkNow);

    // tutorial panel
    const playing = ctx.state === S.PLAYING && !flash;
    el.tutLabel.textContent = key === 'unsure' ? `tutorial step ${n}` : key === 'not_quite'
      ? `⟲ Replaying ${mmss(step.key_clip_start)}–${mmss(step.key_clip_end)}` : 'Tutorial';
    el.frozen.hidden = !([S.WAITING, S.CHECKING, S.CONFIRMING].includes(ctx.state) || flash);
    el.frozen.lastChild.textContent = `paused at the end of step ${n}`;
    el.playBar.hidden = !playing;
    const mistake = ctx.state === S.MISTAKE;
    const clipOn = mistake && clipRunning();
    el.clipBtn.hidden = !mistake || (!clipOn && !clipPaused); // the still frame has its own round Play button
    el.clipBtn.textContent = clipOn ? '⏸ Pause clip' : '▶ Play clip';
    el.loopBar.hidden = !mistake;
    el.loopBar.classList.toggle('running', clipOn);
    el.clipStill.hidden = !(mistake && !clipOn && !clipPaused);
    el.pausedCover.hidden = ctx.state !== S.PAUSED;
    el.tut.classList.toggle('shrink', ['hands', 'blocked', 'checking'].includes(key));

    // live panel
    paintLive(key, ctx.step);
    el.live.dataset.orb = key;
    el.liveLabel.lastChild.textContent = key === 'unsure' ? 'your desk now' : replayMode ? 'You · recorded' : 'You · live';
    const shown = flashLast?.results || [];
    el.passPill.hidden = !flash || !shown.length;
    el.passPill.textContent = `✓ ${shown.filter((r) => r.pass).length} of ${shown.length} passed`;

    // progress, captions, actions
    [...el.segs.children].forEach((seg, i) => {
      seg.className = `seg ${i + 1 < n ? 'past' : i + 1 === n ? 'now' : ''}`;
      seg.disabled = replayMode || ctx.state === S.DONE;
      seg.toggleAttribute('aria-current', i + 1 === n);
    });
    const caption = {
      playing: `${titleOf(n)}.`, hands: `Your turn. ${titleOf(n)}.`,
      blocked: "I can't see part of the work. Move your hand a little.", samFail: "Couldn't check. Hold still for a moment.",
      checking: 'Checking your work…', recheck: 'Looks done.', done: `Nice. ${stepOf(n).done_looks_like}`,
      not_quite: last?.hint || 'Not quite. Compare yours with the video and adjust.',
      paused: 'Paused. Press P or click the video to continue.', cap: 'Live checks are paused. Watch a replay instead.',
    }[key] || '';
    el.caption.hidden = key === 'unsure' || key === 'safety'; // their panels say it
    el.capText.textContent = caption;
    const firstFail = failedResults()[0];
    el.hintTag.hidden = !(mistake && firstFail);
    if (firstFail) {
      el.hintTag.className = `kind-tag ${firstFail.kind}`;
      el.hintTag.textContent = firstFail.kind === 'measured' ? '📏 measured' : '👁 judged';
    }
    el.mistakeActs.hidden = !mistake || replayMode;
    el.ask.hidden = ctx.state !== S.ASKING;
    el.safety.hidden = ctx.state !== S.SAFETY;
    if (ctx.state === S.SAFETY) {
      el.safetyText.textContent = detailOf(ctx.step)?.safety || '';
      el.voiceCap.hidden = true; // the panel already says it, with the button to confirm
      if (safetyShownFor !== ctx.step) { safetyShownFor = ctx.step; el.safety.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' }); }
    }
    if (ctx.state === S.ASKING) {
      el.askQ.textContent = ctx.finalAsk ? finalQuestion(ctx.step) : askQuestion(steps[ctx.step - 1], last);
      el.ask.scrollIntoView({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' });
    }
    if (mistake) el.caption.scrollIntoView({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' });
    const missing = last?.objects?.find((o) => !o.found);
    el.askWhy.textContent = ctx.finalAsk ? 'This step can hurt you if it is wrong, so I never move on by myself.'
      : "I'm not sure, so I'm asking instead of guessing.";
    el.askReason.textContent = ctx.finalAsk ? '⚠️ safety step · the checks passed, and you have the final say'
      : missing ? `SAM 3.1 couldn't find the ${missing.name}`
      : last?.fallback === 'judge_failed' ? "👁 judged · Muse Spark didn't answer, measured checks passed"
        : enabledChecks(ctx.step).some((c) => c.always_ask) ? "👁 judged · the camera can't see this" : '👁 judged · low confidence';
    el.yes.textContent = `Yes, next step${app.keysOn ? ' (Y)' : ''}`;
    el.no.textContent = `No, show me again${app.keysOn ? ' (R)' : ''}`;
    el.yes.disabled = el.no.disabled = replayMode;

    // rail
    el.railCount.textContent = `${ctx.step - 1 + (flash || ctx.state === S.DONE ? 1 : 0)} / ${steps.length}`;
    clear(el.railList).append(...steps.map((st, i) => {
      const num = i + 1;
      const name = railName(steps, num);
      if (num !== ctx.step) {
        return h('div', { class: ['rail-step', num < ctx.step && 'past'] },
          h('span', { class: 'rail-num', 'aria-hidden': 'true' }, num < ctx.step ? '✓' : ''), name);
      }
      return h('div', { class: 'rail-current', 'aria-current': 'step' },
        h('div', { class: 'rail-current-head' }, h('span', { class: 'rail-num now' }, String(num)), name),
        checks.map((c) => {
          const status = chipStatus(c);
          const r = last?.results.find((x) => x.check_id === c.id);
          const value = status === 'pass' || status === 'fail' ? formatValue(c, r) : status === 'unsure' ? 'low' : '';
          return h('div', { class: `chip ${c.kind} ${status}`, title: c.text },
            icon(c.kind === 'measured' ? 'ruler' : 'eye'),
            h('span', { class: 'chip-label' }, checkLabel(c, stepOf(num))),
            h('span', { class: 'chip-value' }, value),
            h('span', { class: 'chip-glyph', 'aria-label': status }, { pending: '', running: '◌', pass: '✓', fail: '✕', skip: '–', unsure: '?' }[status]));
        }));
    }));

    // badges, controls
    // Lit: the models this check is using (while checking) or used (after the verdict).
    const lit = key === 'checking' ? ['sam', ...(sparkNow ? ['spark'] : [])]
      : LIT[key] || (last?.models || []).map(badgeOf);
    const st = last?.stages_ms || {};
    const stageMs = { sam: st.hand != null ? st.hand + (st.objects || 0) : null, spark: st.judge ?? null };
    for (const [k, b] of Object.entries(el.badges)) {
      const on = lit.includes(k) || (k === 'voice' && !!voiceCtl);
      b.classList.toggle('on', on);
      const used = last?.models.some((m) => badgeOf(m) === k);
      b.lastChild.textContent = used ? `${((stageMs[k] ?? last.latency_ms) / 1000).toFixed(1)} s` : '';
    }
    el.badges.voice.hidden = app.config.voice === 'off';
    el.listening.hidden = !listening; // only while the microphone is actually listening (FR-92)
    el.watchNote.hidden = !(watcherOn || replayMode) || ![S.WAITING, S.MISTAKE, S.CHECKING, S.CONFIRMING].includes(ctx.state);
    el.micBtn.textContent = voiceCtl ? '🎙 Voice coach on' : '🎙 Talk to the coach';
    el.muteBtn.textContent = replayVoice ? '🔊 Coach voice on' : '🔇 Coach voice off';
    el.micBtn.setAttribute('aria-pressed', String(!!voiceCtl));
    el.micBtn.hidden = app.config.voice === 'off' || ctx.state === S.DONE || document.documentElement.hasAttribute('data-static');
    el.talkBtn.hidden = voiceCtl?.mode !== 'push_to_talk' || ctx.state === S.DONE;
    el.askForm.hidden = (voiceCtl && voiceCtl.mode !== 'off') || ctx.state === S.DONE;
    el.talkBtn.querySelector('.key').hidden = !app.keysOn;
    const done = ctx.state === S.DONE;
    for (const [k, b] of Object.entries(el.controls)) {
      b.disabled = done || (replayMode && k !== 'pause');
      b.querySelector('.key').hidden = !app.keysOn;
    }
    el.controls.check.disabled ||= !!ctx.inFlight || ctx.state === S.CAPPED;
    el.controls.pause.querySelector('.ctl-label').textContent = ctx.state === S.PAUSED ? 'Resume' : 'Pause';
    if (bannerPause) bannerPause.textContent = ctx.state === S.PAUSED ? '▶ Play' : '⏸ Pause';
    if (drawerOpen) paintDrawer(n);
  }

  function paintDrawer(n) {
    const step = stepOf(ctx.step);
    const M = (t, m, r, status, kind) => h('div', { class: `trace-row ${status}` }, h('span', { class: 'trace-dot', 'aria-hidden': 'true' }),
      h('div', {}, h('div', { class: 'trace-top' }, h('span', { class: 'trace-t' }, t), h('span', { class: 'trace-r mono' }, r)),
        h('div', { class: 'trace-m mono' }, h('span', { class: `kind-tag ${kind}` }, kind), m)));
    const rows = [];
    let decision = ['Waiting', 'pending', 'No check has run for this step yet.'];
    if (last) {
      rows.push(M('Frame taken', 'hands away, or "check me"', '', 'neutral', 'frame'));
      const ms = (k) => (last.stages_ms?.[k] != null ? ` · ${last.stages_ms[k]} ms` : '');  // stages_ms: real checks only
      if (last.models.length) rows.push(M('Hand gate', 'SAM 3.1', (last.hand_blocked ? 'hand in the way' : last.fallback === 'sam_failed' ? 'no answer' : 'no hand') + ms('hand'),
        last.hand_blocked || last.fallback === 'sam_failed' ? 'unsure' : 'pass', 'measured'));
      if (last.stages_ms?.objects != null) {
        const found = last.objects.filter((o) => o.found).length;
        rows.push(M('Find objects', 'SAM 3.1 · one request per object, in parallel', `${found}/${last.objects.length} found${ms('objects')}`,
          found === last.objects.length ? 'pass' : 'unsure', 'measured'));
        rows.push(M('Measure', 'code · geometry on the outlines', `${last.stages_ms.geometry} ms`, 'neutral', 'measured'));
      }
      const spark = last.models.find((m) => badgeOf(m) === 'spark');
      if (spark && last.stages_ms?.judge != null) {
        rows.push(M('Compare with the tutorial', `${spark} · minimal reasoning · your frame vs the step's end frame`,
          `${last.stages_ms.judge} ms`, 'neutral', 'judged'));
      } else if (last.fallback === 'judge_failed') {
        rows.push(M('Compare with the tutorial', 'Muse Spark', 'no answer', 'unsure', 'judged'));
      }
      for (const c of enabledChecks(ctx.step)) {
        const status = chipStatus(c);
        const r = last.results.find((x) => x.check_id === c.id);
        if (c.kind === 'judged' && r) {
          rows.push(M(checkLabel(c, step), `Muse Spark · ${r.reason || ''}`,
            `${formatValue(c, r)} · confidence ${r.confidence.toFixed(2)}`, status, 'judged'));
        } else {
          rows.push(M(checkLabel(c, step), c.kind === 'measured' ? 'code · from SAM 3.1 outlines'
            : c.always_ask ? "not sent: the camera can't see this" : 'Muse Spark',
          status === 'pass' || status === 'fail' ? formatValue(c, r) : status, status, c.kind));
        }
      }
      const missing = last.objects.find((o) => !o.found);
      decision = {
        done: ['Pass', 'pass', `All ${last.results.length} checks passed. ${last.results.every((r) => r.kind === 'measured') ? 'Re-check done.' : 'Double-checking, then the next step.'}`],
        wrong: ['Not quite', 'fail', 'A check failed. Opened the mistake replay.'],
        unsure: ['Ask you', 'unsure', missing ? 'SAM 3.1 missed a piece, so the measured checks could not run.'
          : last.fallback === 'judge_failed' ? "Measured checks passed; Muse Spark didn't answer."
            : enabledChecks(ctx.step).some((c) => c.always_ask) ? "The camera can't see this, so I'm asking."
              : `Measured checks ${last.results.some((r) => r.kind === 'measured' && !r.pass) ? 'failed' : 'passed'}; Muse Spark's lowest confidence was ${
                Math.min(...last.results.filter((r) => r.kind === 'judged').map((r) => r.confidence)).toFixed(2)}.`],
        waiting: ['Waiting', 'pending', last.hand_blocked ? 'A hand covers the work area. Not counted as a check.' : "SAM 3.1 didn't answer. I'll try again when you're still."],
      }[last.verdict];
    }
    clear(el.drawer).append(
      h('div', { class: 'drawer-head' },
        h('div', {}, h('h2', { class: 'drawer-title' }, 'How it decided'), h('div', { class: 'mono dim small' }, `step ${n} · last check`)),
        h('button', { class: 'btn-close', 'aria-label': 'Close', onclick: () => toggleDrawer(false) }, '✕')),
      h('div', { class: 'trace' }, rows),
      h('div', { class: `decision ${decision[1]}` }, h('span', { class: 'mono dim small' }, 'decision'),
        h('span', { class: 'decision-text' }, decision[0]), h('span', { class: 'decision-sub' }, decision[2]),
        last && h('span', { class: 'mono dim small' }, `${last.latency_ms} ms · $${last.cost_usd.toFixed(4)} · ${last.models.join(', ')}`)));
  }

  function toggleDrawer(force) {
    drawerOpen = force ?? !drawerOpen;
    el.drawer.hidden = !drawerOpen;
    el.drawerBtn.setAttribute('aria-expanded', String(drawerOpen));
    paint();
  }

  // ---- pacing ----
  async function dispatch(ev) {
    if (finished) return;
    const before = ctx;
    const r = transition(ctx, ev);
    ctx = r.ctx;
    if (before.state !== ctx.state) onChange(before);
    paint();
    for (const fx of r.effects) await perform(fx, ev, before);
  }

  function onChange(before) {
    if (ctx.state === S.PAUSED) { player.pause(); return; }
    if (before.state === S.PAUSED) { player.resume(); }
    // The learner's turn: hold the step's end frame, even if the video hadn't reached it (replay skips, Check me).
    if (before.state === S.PLAYING && ctx.state !== S.PLAYING) { player.freeze(stepOf(ctx.step)); flash = null; }
    if (before.state === S.MISTAKE && ctx.state !== S.MISTAKE) player.freeze(stepOf(ctx.step));
    if (before.step !== ctx.step) { resetWatch(); if (!replayMode) saveProgress(app.tutorialId, ctx.step, steps.length, meta.title); }
    if (ctx.state === S.SAFETY && before.state !== S.SAFETY && before.state !== S.PAUSED) {
      log('safety_shown', ctx.step, { hazard: hazard(ctx.step) });
      coachSay(`${T('safety_first')} ${detailOf(ctx.step)?.safety || ''} ${T('safety_confirm')}`, true, true, ctx.step, true);
    }
    if ((before.state === S.PLAYING || before.state === S.SAFETY) && ctx.state === S.WAITING) {
      resetWatch();
      log('step_wait', ctx.step); motion?.rearm(); // your turn: move first
      // a hazard is said first; a gated one was already said and confirmed
      const safety = before.state === S.SAFETY ? '' : detailOf(ctx.step)?.safety;
      coachSay(`${before.state === S.SAFETY ? `${T('safe_thanks')} ` : ''}${safety ? `${safety} ` : ''}${T('your_turn')} ${directions(ctx.step)}`,
        true, !!voiceCtl || !!safety, ctx.step);
    }
    if (ctx.state === S.MISTAKE) {
      coachSay(mistakeLine(last), true, !!voiceCtl, ctx.step); // the caption shows it anyway
      clipPaused = false; clipPlay = false;
      player.loop(stepOf(ctx.step));
      if (reducedMotion()) player.pause();
      log('mistake_shown', ctx.step, { failed_checks: failedResults().map((x) => x.check_id) });
    }
    if (ctx.state === S.ASKING) log('ask_shown', ctx.step);
  }

  async function perform(fx, ev, before) {
    if (fx.type === 'play') {
      flashLast = last;
      last = null;
      if (fx.reason === 'advance' || fx.reason === 'override') log('advance', before.step);
      log('step_play', fx.step, { reason: fx.reason });
      const start = () => {
        flash = null;
        const paused = ctx.state === S.PAUSED && ctx.prev === S.PLAYING; // paused during the "step done" flash
        if (ctx.step === fx.step && (ctx.state === S.PLAYING || paused)) {
          player.playStep(stepOf(fx.step));
          if (paused) player.pause(); // resume() continues from the step's start
        }
        paint();
      };
      if ((fx.reason === 'advance' || fx.reason === 'override') && ev.type !== 'next') { flash = before.step; paint(); later(start, ADVANCE_FLASH_MS); } else start();
    } else if (fx.type === 'check') {
      await runCheck(fx);
    } else if (fx.type === 'schedule_recheck') {
      if (!replayMode) later(() => dispatch({ type: 'recheck_due' }), app.config.motion.recheck_delay_ms);
    } else if (fx.type === 'final_ask') {
      log('final_ask', ctx.step, { hazard: hazard(ctx.step) });
      coachSay(T('confirm_done', { text: finalQuestion(ctx.step) }), true, true, ctx.step);
    } else if (fx.type === 'capped') {
      log('cap_reached', ctx.step, { code: fx.code });
      showOverlay('cap');
    } else if (fx.type === 'done') {
      flash = before.step; flashLast = last; paint(); // no `advance` event for the last step: the summary adds it (data dictionary)
      later(() => finish('completed'), ADVANCE_FLASH_MS);
    }
  }

  async function runCheck(fx) {
    const step = ctx.step;
    const checkNo = nextCheckNo(step);
    const triggerAt = Date.now();
    const source = camVideo || recFrame || el.liveMedia.querySelector('svg');
    try {
      const frame = await grabFrame(source);
      const res = await api.check(sid, frame, { step_id: step, kind: fx.kind, check_no: checkNo, trigger: fx.trigger, trigger_at: triggerAt });
      if (finished || ctx.inFlight?.id !== fx.id) return; // stale: the learner moved on
      const prevJudged = fx.confirm && last ? last.results.filter((x) => x.kind === 'judged') : [];
      last = { ...res, results: [...res.results, ...prevJudged] };
      log(fx.confirm ? 'recheck' : 'check', step, {
        kind: fx.kind, check_no: checkNo, trigger: fx.trigger, verdict: res.verdict, fallback: res.fallback,
        results: res.results.map(({ check_id, pass, value, confidence }) => ({ check_id, pass, value, confidence })),
        latency_ms: res.latency_ms, client_latency_ms: Date.now() - triggerAt, cost_usd: res.cost_usd, models: res.models,
      });
      motion?.rearm(res.fallback !== 'sam_failed'); // the next automatic check needs motion; a failed one retries when still
      const measuredFail = res.results.some((x) => x.kind === 'measured' && !x.pass);
      lastCheck = `${res.verdict}${res.hint ? `: ${res.hint}` : ''}`;
      const asked = voiceAsked; voiceAsked = false;
      if (!fx.confirm) coachVerdict(res, step, asked);
      await dispatch({ type: 'verdict', id: fx.id, verdict: res.verdict, measuredFail });
    } catch (err) {
      if (finished) return;
      log(['cap_reached', 'limit_reached'].includes(err.code) ? err.code : 'error', step, { code: err.code });
      if (!['cap_reached', 'limit_reached', 'check_in_progress'].includes(err.code)) toast(err.message);
      if (replayMode && err.code === 'not_found') showOverlay('model', { title: 'The replay stopped', body: err.message });
      motion?.rearm(false);
      await dispatch({ type: 'error', id: fx.id, code: err.code });
    }
  }

  function act(name, via) {
    if (replayMode || ctx.state === S.DONE) return;
    log('action', ctx.step, { name, via });
    dispatch({ type: name });
  }

  function confirmSafe(via) {
    if (replayMode || ctx.state !== S.SAFETY) return;
    log('safety_confirmed', ctx.step, { via, hazard: hazard(ctx.step) });
    dispatch({ type: 'safety_ok' });
  }

  // "No, it's right": after a check said wrong (or at a question) the learner's word moves on, and the disagreement
  // is logged with what the check said: those are the labels the feedback loop learns from. While the live coach is
  // only talking (no check yet), disagreeing asks the grounded check to look properly instead.
  function override(via) {
    if (replayMode) return;
    const verdict = last ? { verdict: last.verdict, failed: failedResults().map((x) => x.check_id) } : {};
    if (ctx.state === S.MISTAKE || ctx.state === S.ASKING) {
      log('override', ctx.step, { via, state: ctx.state, ...verdict, coach_said: lastSaid.slice(0, 200) });
      coachSay(T('override_ok'), true, true, ctx.step);
      dispatch({ type: 'override' });
    } else if (ctx.state === S.WAITING) {
      log('coach_disputed', ctx.step, { via, coach_said: lastSaid.slice(0, 200) });
      coachSay(T('override_check'), true, true, ctx.step);
      dispatch({ type: 'check_me' });
    }
  }

  function finalQuestion(n) {
    return detailOf(n)?.check_yourself || stepOf(n).done_looks_like || stepOf(n).instruction;
  }

  function jump(n, via = 'button') {
    if (replayMode || ctx.state === S.DONE) return;
    log('action', ctx.step, { name: 'jump', to: n, via });
    dispatch({ type: 'jump', step: n });
  }

  function answer(yes, via) {
    if (replayMode || ctx.state !== S.ASKING) return;
    log('ask_answer', ctx.step, { answer: yes, via });
    dispatch({ type: yes === 'yes' ? 'ask_yes' : 'ask_no' });
  }

  function togglePause(via) {
    if (ctx.state === S.DONE) return;
    const pausing = ctx.state !== S.PAUSED;
    if (replay) (pausing ? replay.pause() : replay.resume());
    else log('action', ctx.step, { name: pausing ? 'pause' : 'resume', via });
    dispatch({ type: pausing ? 'pause' : 'resume' });
  }

  async function finish(reason) {
    if (finished) return;
    finished = true;
    completed = reason === 'completed';
    if (completed && !replayMode) clearProgress(app.tutorialId);
    voiceCtl?.stop();
    replay?.stop();
    motion?.stop();
    // Finished: the coach's last line ("That was the last step. Great work.") ends here, not over the summary
    const [summary] = await Promise.all([api.endSession(sid, reason).catch((err) => { toast(err.message); return null; }),
      completed ? whenQuiet(6000) : null]);
    app.summary = summary;
    app.summaryTitle = meta.title;
    app.summaryPlan = plan;
    nav('summary');
  }

  // ---- voice (live only, after the S6 switch; off in replay) ----
  let listening = false, questionNo = 0;
  let lastSaid = '', lastCheck = '', voiceAsked = false;
  let replayVoice = true;  // replays speak the coach's lines out loud (the 🔇 button mutes them)
  const convo = [];  // the conversation, in this tab only; the coach gets the last 8 turns with each question
  let voiceCtl = null;
  const voiceOn = () => app.config.voice !== 'off' && (voiceCtl = createVoice({
    sid, mode: app.config.voice, on: {
      listening: (on) => { listening = on; paint(); },
      mode: () => paint(),
      caption: (text) => { el.heard.textContent = text; el.reply.textContent = ''; el.voiceCap.hidden = false; },
      command: (name, text) => {
        el.heard.textContent = text; el.reply.textContent = ''; el.voiceCap.hidden = false;
        remember('user', text);
        if (name === 'repeat') { coachSay(lastSaid || `${T('step_label', { n: ctx.step })}. ${directions(ctx.step)}`); return; }
        if (ctx.state === S.SAFETY && ['safe', 'yes', 'play', 'check_me'].includes(name)) { confirmSafe('voice'); return; }
        if (name === 'safe') { askQuestionAloud(text); return; }
        if (name === 'override') { if (ctx.state === S.ASKING && !ctx.finalAsk) answer('yes', 'voice'); else override('voice'); return; }
        if (name === 'yes' || name === 'no') {
          if (ctx.state === S.ASKING) answer(name, 'voice'); else askQuestionAloud(text);
          return;
        }
        if (replayMode && name !== 'pause' && name !== 'play') { askQuestionAloud(text); return; } // the recording drives the steps
        // navigation by voice: "go back", "go to step three", "skip this step"
        const to = name === 'back' ? ctx.step - 1 : name.startsWith('step_') ? Number(name.slice(5)) : null;
        if (to !== null) {
          if (to >= 1 && to <= steps.length) { coachSay(T('okay_step', { n: to }), true, true, to); jump(to, 'voice'); }
          else coachSay(to < 1 ? T('first_step') : T('only_steps', { n: steps.length }));
          return;
        }
        if (name === 'check_me') voiceAsked = true;
        if (name === 'pause' || name === 'play') { if ((ctx.state === S.PAUSED) === (name === 'play')) togglePause('voice'); }
        else act(name, 'voice');
      },
      question: (text) => askQuestionAloud(text),
      notice: (msg) => toast(msg),
    },
  }));
  // The 🎙 button on this screen: turn the coach on or off at any time (live and replay)
  async function toggleVoice() {
    if (voiceCtl) { voiceCtl.stop(); voiceCtl = null; listening = false; app.voiceOn = false; hush(); paint(); return; }
    try { await askMic(); } catch { toast('The microphone is blocked. Allow it in the address bar, then try again.'); return; }
    app.voiceOn = true;
    voiceOn();
    coachSay(replayMode ? T('listening_replay') : T('listening', { n: ctx.step, text: directions(ctx.step) }));
    paint();
  }
  el.micBtn.addEventListener('click', toggleVoice);
  el.muteBtn.addEventListener('click', () => { replayVoice = !replayVoice; if (!replayVoice) hush(); paint(); });
  if (app.voiceOn) voiceOn();

  async function askQuestionAloud(text, spoken = true) {
    const n = ++questionNo;
    const history = convo.slice(-8);
    if (convo[convo.length - 1]?.text !== text) remember('user', text);
    el.heard.textContent = text;
    el.reply.textContent = 'Muse Spark is looking…';
    el.voiceCap.hidden = false;
    try {
      const frame = await grabFrame(camVideo || recFrame || el.liveMedia.querySelector('svg'));
      const res = await api.question(sid, frame, { step_id: ctx.step, question: text, question_no: n, history,
        last_check: lastCheck });
      if (n !== questionNo || finished) return;
      coachSay(res.answer, spoken, true); // a typed question gets its answer in the captions only
    } catch (err) {
      if (n === questionNo) el.reply.textContent = err.message;
    }
  }

  // ---- the coach's voice: one place that speaks, captions and remembers what was said ----
  function remember(role, text) { if (text) { convo.push({ role, text }); if (convo.length > 16) convo.shift(); } }
  // `tag`: the step a line is about. A line that has started always finishes; waiting lines about another step drop.
  function coachSay(text, spoken = true, show = !!voiceCtl, tag = null, urgent = false) {
    if (!text || finished) return;
    lastSaid = text;
    remember('coach', text);
    if (show) { el.reply.textContent = text; el.voiceCap.hidden = false; el.voiceCap.classList.toggle('urgent', urgent); }
    if ((voiceCtl || (replayMode && replayVoice)) && spoken) speak(text, tag, urgent); // replays narrate unless muted
  }

  // ---- the live coach: a fresh look every 2–3 s while you move (every 8 s when still), like a teacher watching ----
  // Looks overlap: a new one starts every LOOK_MS while you move (up to 2 in flight), so a mistake is heard ~1.5–3 s
  // after it happens instead of waiting for the previous look to finish. An answer older than one already shown is dropped.
  // The server sets the pace (next_look_ms): every 1.5 s when the API key has room, slower when many people are
  // practising at once; a look it had no room for comes back `busy` and is simply skipped.
  let LOOK_MS = 1500;
  const STILL_MS = 8000, MAX_LOOKS = 2;
  let lookSeq = 0, shownSeq = 0;
  let lastLook = 0, looking = 0, stepStart = Date.now(), lastLine = '', lastLineAt = 0, lastCantSee = 0;
  let doneCooldown = 0, watcherStopped = false, lastStatus = '';
  const notes = [], saidLines = [];
  function resetWatch() { notes.length = 0; saidLines.length = 0; lastLine = ''; stepStart = Date.now(); lastLook = Date.now(); }
  async function look() {
    looking += 1;
    lastLook = Date.now();
    const step = ctx.step, seq = ++lookSeq;
    el.watchNote.dataset.state = 'looking';
    try {
      const frame = await grabFrame(camVideo);
      const res = await api.watch(sid, frame, { step_id: step, recent: notes.slice(-5), said: saidLines.slice(-3),
        elapsed_s: Math.round((Date.now() - stepStart) / 1000), encourage: Date.now() - lastLineAt > 20000 });
      if (res.next_look_ms) LOOK_MS = Math.max(1500, Math.min(10000, res.next_look_ms));
      if (res.status === 'busy') return;
      if (finished || step !== ctx.step || seq < shownSeq) return; // a newer look already answered
      shownSeq = seq;
      if (res.observation) { notes.push(res.observation); if (notes.length > 8) notes.shift(); }
      el.watchNote.textContent = res.observation ? `👁 ${res.observation}` : '👁 Watching…';
      el.watchNote.dataset.state = res.status;
      onLook(res, step);
    } catch (err) {
      if (['cap_reached', 'limit_reached', 'no_api_key'].includes(err.code)) { watcherStopped = true; toast(err.message); }
    } finally { looking -= 1; }
  }
  function onLook(res, step) {
    if (res.status === 'looks_done') { // the model thinks it's done: the grounded check (SAM 3.1) confirms or not
      if (Date.now() > doneCooldown && !ctx.inFlight && [S.WAITING, S.MISTAKE].includes(ctx.state)) {
        doneCooldown = Date.now() + 6000;
        lastStatus = 'looks_done';
        coachSay(T('looks_good'), true, true, step); // replaces a stale "Oh, wait" on screen
        dispatch({ type: 'check_me' });
      }
      return;
    }
    const line = (res.say || '').trim();
    if (!line || line === lastLine) return;
    if (res.status === 'cant_see' && Date.now() - lastCantSee < 10000) return; // don't nag about the camera
    // overlapping looks often see the same mistake: say it once, again only if it's still there 6 s later
    if (res.status === 'mistake' && lastStatus === 'mistake' && Date.now() - lastLineAt < 6000) return;
    lastStatus = res.status;
    if (res.status === 'cant_see') lastCantSee = Date.now();
    lastLine = line; lastLineAt = Date.now();
    saidLines.push(line);
    coachSay(line, true, true, step, res.status === 'mistake');
  }
  const watchTimer = watcherOn ? setInterval(() => {
    if (watcherStopped || finished || looking >= MAX_LOOKS || document.hidden || ctx.inFlight) return;
    if (![S.WAITING, S.MISTAKE].includes(ctx.state)) return;  // never during a safety step: the learner confirms it
    const since = Date.now() - lastLook;
    if ((Date.now() - lastMove < LOOK_MS * 2 && since >= LOOK_MS) || (!looking && since >= STILL_MS)) look();
  }, 250) : null;
  // While the coach talks, the tutorial video is turned down so the two never talk over each other
  const stopDucking = onSpeech((talking) => el.tut.querySelectorAll('video').forEach((v) => { v.volume = talking ? 0.15 : 1; }));
  function mistakeLine(res) {
    const seen = res?.results?.find((x) => x.kind === 'judged' && !x.pass && x.reason)?.reason;
    // the check's reason and hint are English; another language gets the step's translated directions instead
    const see = seen && lang === 'en' ? `I can see ${seen.replace(/^(the )?learner('s)? (picture|photo|image) (shows|has)\s*/i, '').replace(/\.$/, '')}. ` : '';
    return `${T('not_quite')} ${see}${(lang === 'en' && res?.hint) || T('try_again', { text: directions(ctx.step) })}`;
  }
  function coachVerdict(res, n, asked) {
    if (res.verdict === 'done') {
      const next = stepOf(n + 1);
      if (ctx.confirmDone.includes(n)) return; // a safety step: the learner is asked first (final_ask)
      coachSay(next ? T('yes_next', { n: n + 1, text: directions(n + 1) }) : T('yes_last'), true, !!voiceCtl, next ? n + 1 : n);
    } else if (res.verdict === 'unsure') {
      coachSay(`${T('not_sure')} ${lang === 'en' ? askQuestion(stepOf(n), last) : finalQuestion(n)} ${T('say_yes_no')}`);
    } else if (res.verdict === 'waiting' && asked) {
      coachSay(res.hand_blocked ? T('cant_see_yet') : T('couldnt_check'));
    } // wrong: spoken when the mistake screen opens (mistakeLine)
  }

  // ---- wiring ----
  const onKeyUp = (e) => { if (e.key.toLowerCase() === 'v') voiceCtl?.release(); };
  window.addEventListener('keyup', onKeyUp);
  setShortcuts((k) => {
    if (k === 'v') { if (voiceCtl?.mode !== 'push_to_talk') return false; voiceCtl.press(); return true; }
    if (k === 'd') { toggleDrawer(); return true; }
    if (k === 'p') { togglePause('key'); return true; }
    const map = { c: 'check_me', a: 'again', n: 'next', s: 'skip' };
    if (map[k]) { act(map[k], 'key'); return true; }
    if ((k === 'y' || k === 'r') && ctx.state === S.ASKING) { answer(k === 'y' ? 'yes' : 'no', 'key'); return true; }
    if (k === 'y' && ctx.state === S.SAFETY) { confirmSafe('key'); return true; }
    return false;
  });
  const onEscape = () => drawerOpen && toggleDrawer(false);
  const onEnd = () => finish('left');
  const onKeys = () => paint();
  const onHide = () => { if (!finished) { finished = true; api.endBeacon(sid); } };
  window.addEventListener('fa:escape', onEscape);
  window.addEventListener('fa:end-session', onEnd);
  window.addEventListener('fa:keys-changed', onKeys);
  window.addEventListener('pagehide', onHide);
  const heartbeat = setInterval(() => log('heartbeat', ctx.step), 60000);

  const replay = replayMode ? createReplay({ timeline, dispatch, onFinish: () => finish('completed'), onEvent: (e) => showRecordedAt(e.off),
    onTick: showRecordedAt,
    onQuestion: (text) => askQuestionAloud(text, false),
    onCoach: (d, step) => {  // a recorded look of the live coach, replayed like the live one (it speaks, the note updates)
      if (d.observation) { el.watchNote.textContent = `👁 ${d.observation}`; el.watchNote.dataset.state = d.status; }
      if (d.say) coachSay(d.say, true, true, step, d.status === 'mistake');
    } }) : null;
  // ---- framing: light and focus, measured on the phone (free, instant); said once, then again only if it persists ----
  let lastFraming = 0;
  const framingTimer = camVideo ? setInterval(() => {
    if (finished || document.hidden) return;
    const q = frameQuality(camVideo);
    const issue = !q ? null : q.dark ? 'too_dark' : q.blurry ? 'too_blurry' : null;
    el.framing.hidden = !issue;
    el.framing.textContent = issue === 'too_dark' ? '💡 Too dark' : '🔍 Blurry';
    if (issue && Date.now() - lastFraming > 30000 && [S.PLAYING, S.WAITING, S.MISTAKE].includes(ctx.state)) {
      lastFraming = Date.now();
      log('framing', ctx.step, { issue, brightness: q.brightness, sharpness: q.sharpness });
      coachSay(T(issue), true, true, null);
    }
  }, 4000) : null;

  // ---- keep the screen on: hands are busy, and a phone that locks mid-step breaks hands-free ----
  let wakeLock = null;
  const keepAwake = async () => {
    if (finished || document.hidden || wakeLock || !navigator.wakeLock) return;
    try { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); }
    catch { /* battery saver or not allowed: the lesson still works */ }
  };
  const onVisible = () => { if (!document.hidden) keepAwake(); };
  document.addEventListener('visibilitychange', onVisible);
  keepAwake();

  const resumed = ctx.step > 1;
  log('step_play', ctx.step, { reason: resumed ? 'resume' : 'first' });
  if (resumed) { log('resumed', ctx.step); coachSay(T('welcome_back', { n: ctx.step }), true, true, ctx.step); }
  if (!replayMode) saveProgress(app.tutorialId, ctx.step, steps.length, meta.title);
  player.playStep(stepOf(ctx.step));
  paint();
  replay?.start();
  motion?.start();

  return () => {
    finished = true;
    clearInterval(watchTimer);
    clearInterval(framingTimer);
    document.removeEventListener('visibilitychange', onVisible);
    wakeLock?.release().catch(() => {});
    voiceCtl?.stop();
    stopDucking();
    hush(); // the coach never talks over another page (a finished lesson already waited for its last line)
    window.removeEventListener('keyup', onKeyUp);
    replay?.stop();
    motion?.stop();
    player.destroy();
    timers.forEach(clearTimeout);
    clearInterval(heartbeat);
    setShortcuts(null);
    window.removeEventListener('fa:escape', onEscape);
    window.removeEventListener('fa:end-session', onEnd);
    window.removeEventListener('fa:keys-changed', onKeys);
    window.removeEventListener('pagehide', onHide);
  };
}

// English defaults for the coach's fixed lines (the server's copy: config/phrases.json), used until /api/phrases answers
const PHRASES_EN = {
  your_turn: 'Your turn.', safety_first: 'Safety first.', safety_confirm: 'Say "ready" or tap "It\'s safe" when you\'ve done that.',
  not_quite: 'Not quite.', try_again: 'Try again: {text}', yes_next: "Yes, that's right! Next up, step {n}: {text}",
  yes_last: "Yes, that's right! That was the last step. Great work.", not_sure: "I'm not completely sure.", say_yes_no: 'Say yes or no.',
  cant_see_yet: "I can't see your work yet. Move your hands out of the way and ask me again.",
  couldnt_check: "I couldn't check just now. Hold still and ask me again.", looks_good: 'Looks good. Let me make sure.',
  okay_step: 'Okay, step {n}.', first_step: "We're already on the first step.", only_steps: 'There are only {n} steps.',
  listening: "I'm listening. Step {n}: {text}", listening_replay: "I'm listening. Ask me anything about this lesson.",
  welcome_back: 'Welcome back. Picking up at step {n}.',
  confirm_done: 'This one matters, so you have the final say. Check: {text} Is that right? Say yes or no.',
  override_ok: 'Okay, you know your setup. Moving on.', override_check: 'Okay, let me look again properly.',
  too_dark: "It's a bit dark for me. Can you turn on a light, or move closer to one?",
  too_blurry: "The picture's blurry. Hold the camera steady, or tap the screen to focus.", safe_thanks: 'Thanks. Go ahead.',
  step_label: 'Step {n}',
};

function railName(steps, n) {
  const fresh = [...newObjects(steps, n)];
  const name = fresh[0] || steps[n - 1].instruction;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

// S9 names the check it is unsure about: the least confident judged result, else the step's first judged check.
function askQuestion(step, last) {
  const judged = (last?.results || []).filter((r) => r.kind === 'judged').sort((a, b) => (a.confidence ?? 1) - (b.confidence ?? 1));
  const check = step.checks.find((c) => c.id === judged[0]?.check_id) || step.checks.find((c) => c.kind === 'judged');
  if (!check) return 'Does yours look like this?';
  return check.question || `${check.text.replace(/\s*\(.*\)\s*$/, '')}. Does yours look like this?`;
}
