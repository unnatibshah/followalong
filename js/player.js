// Tutorial player, always silent (the coach is the only voice): loops the current step beside the learner, or the
// 3-second key clip on a mistake.
// Wraps <video>; for a tutorial with no MP4 (config `stand_in_media`) it draws the stand-in scene and simulates the timing.
import { h } from './ui/dom.js';
import { thermostatScene } from './thermostatscene.js';
import { app } from './state.js';

const STAND_IN_MAX_S = 3; // a stand-in loop lasts at most this long

export function createPlayer({ src, standIn, onTime }) {
  let mode = 'idle'; // idle | loop
  let range = [0, 0];
  let paused = false;

  if (standIn) {
    const el = h('div', { class: 'player' }, thermostatScene({ tutorial: app.tutorialId, variant: 'end', surface: 'studio', tags: false }));
    let t0 = 0, done = 0, total = 0, raf = 0;
    const tick = () => {
      if (paused || mode === 'idle') return;
      const frac = Math.min(1, (done + (performance.now() - t0) / 1000) / total);
      onTime?.(range[0] + frac * (range[1] - range[0]), frac);
      if (frac >= 1) { done = 0; t0 = performance.now(); } // loop
      raf = requestAnimationFrame(tick);
    };
    const run = (m, a, b) => {
      cancelAnimationFrame(raf);
      mode = m; range = [a, b]; paused = false; done = 0; t0 = performance.now();
      total = Math.min(STAND_IN_MAX_S, Math.max(0.5, b - a));
      raf = requestAnimationFrame(tick);
    };
    return {
      el,
      loop: (step) => run('loop', step.key_clip_start, step.key_clip_end),
      loopStep: (step) => run('loop', step.start, Math.max(step.start + 1, step.done_at || step.end)), // the reference during your turn
      pause() { if (!paused && mode !== 'idle') { paused = true; done += (performance.now() - t0) / 1000; cancelAnimationFrame(raf); } },
      resume() { if (paused) { paused = false; t0 = performance.now(); raf = requestAnimationFrame(tick); } },
      destroy() { mode = 'idle'; cancelAnimationFrame(raf); },
    };
  }

  // Muted: the original narrator never talks over the coach, and silent video autoplays everywhere
  const video = h('video', { class: 'player-video', src, muted: true, playsInline: true, preload: 'auto', 'aria-label': 'Tutorial video, silent' });
  const play = () => video.play().catch(() => {}); // a pause during buffering rejects: harmless
  const el = h('div', { class: 'player' }, video);
  const rvfc = 'requestVideoFrameCallback' in video;
  let cb = 0;
  const schedule = () => { if (rvfc) { video.cancelVideoFrameCallback(cb); cb = video.requestVideoFrameCallback(watch); } };
  function watch() {
    if (mode === 'idle' || video.seeking) { if (mode !== 'idle') schedule(); return; }
    const t = video.currentTime;
    onTime?.(t, Math.min(1, Math.max(0, (t - range[0]) / (range[1] - range[0] || 1))));
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
    loop: (step) => run('loop', step.key_clip_start, step.key_clip_end),
    loopStep: (step) => run('loop', step.start, Math.max(step.start + 1, step.done_at || step.end)), // the reference during your turn
    pause() { paused = true; video.pause(); },
    resume() { if (paused && mode !== 'idle') { paused = false; play(); schedule(); } },
    destroy() { halt(); video.removeAttribute('src'); video.load(); },
  };
}
