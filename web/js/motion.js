// Motion detection (technical design §4.1). Settings come from /api/config `motion`:
// every `sample_ms`, the source is drawn to a sample_w×sample_h canvas and converted to greyscale; a pixel has changed
// when its brightness moved by more than `pixel_delta` (of 255); the frame is moving when more than `still_fraction`
// of its pixels changed. "Still" fires once the scene has stayed under that for `still_ms`. After it fires (or after
// rearm()), the scene must move again before the next "still" (no repeated checks on an unchanged scene).
//
// The pure parts (greyscale, changedFraction, createDetector) need no DOM, so `node --test` covers them.

/** RGBA bytes → one brightness byte per pixel (Rec. 601 weights). */
export function greyscale(rgba) {
  const out = new Uint8Array(rgba.length / 4);
  for (let i = 0, j = 0; j < out.length; i += 4, j += 1) out[j] = (rgba[i] * 299 + rgba[i + 1] * 587 + rgba[i + 2] * 114) / 1000;
  return out;
}

/** Share of pixels whose brightness changed by more than `pixelDelta`. */
export function changedFraction(prev, cur, pixelDelta) {
  let n = 0;
  for (let i = 0; i < cur.length; i += 1) if (Math.abs(cur[i] - prev[i]) > pixelDelta) n += 1;
  return cur.length ? n / cur.length : 0;
}

/**
 * The still timer over a stream of greyscale frames. push(grey, nowMs) → {fraction, moving, still}; `still` is true
 * on the one frame where the scene has been still for `still_ms` while armed.
 */
export function createDetector({ pixel_delta, still_fraction, still_ms }) {
  let prev = null, since = null, armed = true;
  return {
    push(grey, now) {
      const fraction = prev && prev.length === grey.length ? changedFraction(prev, grey, pixel_delta) : 0;
      const first = !prev;
      prev = grey;
      const moving = fraction > still_fraction;
      if (moving) { since = null; armed = true; return { fraction, moving, still: false }; }
      if (since === null) since = now;
      const still = !first && armed && now - since >= still_ms;
      if (still) armed = false;
      return { fraction, moving, still };
    },
    // After a check: the next "still" needs motion first. needMotion=false restarts the still period instead
    // (after a failed check, so it tries again at the next still moment without the learner moving).
    rearm(needMotion = true) {
      if (needMotion) armed = false;
      else { armed = true; since = null; }
    },
  };
}

/**
 * @param {{source: HTMLVideoElement|HTMLImageElement, settings: object, onStill: () => void,
 *          onLevel?: (fraction: number, moving: boolean) => void}} opts
 * @returns {{start(): void, stop(): void, rearm(needMotion?: boolean): void}}
 */
export function createMotion({ source, settings, onStill, onLevel }) {
  const { sample_w: w, sample_h: hgt, sample_ms } = settings;
  const canvas = Object.assign(document.createElement('canvas'), { width: w, height: hgt });
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const det = createDetector(settings);
  let timer = null, samples = 0, since = performance.now();
  const ready = () => (source instanceof HTMLVideoElement ? source.readyState >= 2 && source.videoWidth
    : source.complete && source.naturalWidth);
  const tick = () => {
    if (!ready()) return;
    ctx.drawImage(source, 0, 0, w, hgt);
    const r = det.push(greyscale(ctx.getImageData(0, 0, w, hgt).data), performance.now());
    onLevel?.(r.fraction, r.moving);
    if (r.still) onStill();
    if (settings.debug && (samples += 1) && performance.now() - since >= 1000) { // PERF-06 (FA_LOG_LEVEL=debug)
      console.debug(`[motion] ${((samples * 1000) / (performance.now() - since)).toFixed(1)} samples/s`);
      samples = 0; since = performance.now();
    }
  };
  return {
    start() { if (!timer) timer = setInterval(tick, sample_ms); },
    stop() { clearInterval(timer); timer = null; },
    rearm: (needMotion) => det.rearm(needMotion),
  };
}
