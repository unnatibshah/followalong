// Tutorial player: plays one step's range and pauses at its end, or loops the 3-second key clip.
// Wraps <video>; for a tutorial with no MP4 (config `stand_in_media`) it draws the stand-in scene and simulates the timing.
import { h } from './ui/dom.js';
import { thermostatScene } from './thermostatscene.js';
import { app } from './state.js';

const STAND_IN_MAX_S = 3; // a stand-in step "plays" for at most this long, so the loop is quick to watch

export function createPlayer({ src, standIn, onEnd, onTime, onSoundBlocked }) {
  let mode = 'idle'; // idle | step | loop
  let range = [0, 0];
  let paused = false;

  if (standIn) {
    const el = h('div', { class: 'player' }, thermostatScene({ tutorial: app.tutorialId, variant: 'end', surface: 'studio', tags: false }));
    let t0 = 0, done = 0, total = 0, raf = 0;
    const tick = () => {
      if (paused || mode === 'idle') return;
      const frac = Math.min(1, (done + (performance.now() - t0) / 1000) / total);
      onTime?.(range[0] + frac * (range[1] - range[0]), frac);
      if (frac >= 1) {
        if (mode === 'step') { mode = 'idle'; onEnd?.(); return; }
        done = 0; t0 = performance.now(); // loop
      }
      raf = requestAnimationFrame(tick);
    };
    const run = (m, a, b) => {
      cancelAnimationFrame(raf);
      mode = m; range = [a, b]; paused = false; done = 0; t0 = performance.now();
      total = m === 'loop' ? 3 : Math.min(STAND_IN_MAX_S, Math.max(0.5, b - a));
      raf = requestAnimationFrame(tick);
    };
    return {
      el,
      sound() {}, // the drawn stand-in has no audio
      playStep: (step) => run('step', step.start, step.end),
      loop: (step) => run('loop', step.key_clip_start, step.key_clip_end),
      loopStep: (step) => run('loop', step.start, Math.max(step.start + 1, step.done_at || step.end)), // the reference during your turn
      pause() { if (!paused && mode !== 'idle') { paused = true; done += (performance.now() - t0) / 1000; cancelAnimationFrame(raf); } },
      resume() { if (paused) { paused = false; t0 = performance.now(); raf = requestAnimationFrame(tick); } },
      freeze() { mode = 'idle'; cancelAnimationFrame(raf); },
      destroy() { mode = 'idle'; cancelAnimationFrame(raf); },
    };
  }

  // Starts muted (browsers only autoplay silent video); the lesson turns the sound on after the learner's tap. If the
  // browser still refuses sound (no tap yet, e.g. a reload straight into a lesson), it plays silently and says so.
  const video = h('video', { class: 'player-video', src, muted: true, playsInline: true, preload: 'auto', 'aria-label': 'Tutorial video' });
  const play = () => video.play().catch(() => {
    if (video.muted) return;
    video.muted = true;
    video.play().catch(() => {});
    onSoundBlocked?.();
  });
  const el = h('div', { class: 'player' }, video);
  const rvfc = 'requestVideoFrameCallback' in video;
  let cb = 0;
  const schedule = () => { if (rvfc) { video.cancelVideoFrameCallback(cb); cb = video.requestVideoFrameCallback(watch); } };
  function watch() {
    if (mode === 'idle' || video.seeking) { if (mode !== 'idle') schedule(); return; }
    const t = video.currentTime;
    onTime?.(t, Math.min(1, Math.max(0, (t - range[0]) / (range[1] - range[0] || 1))));
    if (mode === 'step' && t >= range[1] - 0.2) { video.pause(); mode = 'idle'; onEnd?.(); return; }
    if (mode === 'loop' && t >= range[1]) video.currentTime = range[0];
    schedule();
  }
  if (!rvfc) video.addEventListener('timeupdate', watch);
  const run = (m, a, b) => {
    mode = m; range = [a, b]; paused = false;
    video.currentTime = a;
    play();
    schedule();
  };
  const halt = () => { mode = 'idle'; if (rvfc) video.cancelVideoFrameCallback(cb); video.pause(); };
  return {
    el,
    playStep: (step) => run('step', step.start, step.end),
    loop: (step) => run('loop', step.key_clip_start, step.key_clip_end),
      loopStep: (step) => run('loop', step.start, Math.max(step.start + 1, step.done_at || step.end)), // the reference during your turn
    pause() { paused = true; video.pause(); },
    resume() { if (paused && mode !== 'idle') { paused = false; play(); schedule(); } },
    /** Tutorial sound on or off; call it from a tap (or after one) so the browser allows it. */
    sound(on) { video.muted = !on; if (!on || video.paused || mode === 'idle') return; play(); },
    /** Stop on the step's end frame (the learner's turn), wherever the video was. */
    freeze(step) { halt(); video.currentTime = Math.max(step.start, step.end - 0.1); },
    destroy() { halt(); video.removeAttribute('src'); video.load(); },
  };
}
