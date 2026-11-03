// Two short sounds, so you know without looking: a bright rising pair when a step is done, one soft low note on
// "not quite". Web Audio, no files; silent if the browser has no audio or hasn't had a tap yet.
let ctx = null;

export function chime(kind) {
  try {
    ctx ||= new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') ctx.resume();
    const notes = kind === 'done' ? [[784, 0], [1175, 0.11]] : [[330, 0]]; // G5 → D6; E4
    for (const [hz, at] of notes) {
      const t = ctx.currentTime + at;
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = hz;
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(kind === 'done' ? 0.18 : 0.12, t + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + (kind === 'done' ? 0.5 : 0.35));
      osc.connect(gain).connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.6);
    }
  } catch { /* no Web Audio: the ring and the tick still show it */ }
}
