// AudioWorklet (TD §2 `voice`): the microphone's mono input resampled to 24 kHz, signed 16-bit, posted to the page in
// 80 ms chunks (1,920 samples). A separate file because the CSP allows no inline or blob scripts.

/** Linear-interpolation resampler, Float32 in → Int16 out. Keeps its phase across buffers.
 *  ponytail: no low-pass before decimating 48 → 24 kHz; fine for speech, add a filter if transcripts suffer. */
export class Resampler {
  constructor(inRate, outRate = 24000) { this.step = inRate / outRate; this.t = 0; this.prev = 0; }

  push(x) {
    const out = [];
    while (this.t <= x.length - 1) {
      const i = Math.floor(this.t), f = this.t - i;
      const a = i < 0 ? this.prev : x[i];
      const b = x[i + 1] ?? a;
      const v = Math.max(-1, Math.min(1, a + (b - a) * f));
      out.push(Math.round(v * 0x7fff));
      this.t += this.step;
    }
    this.t -= x.length;
    if (x.length) this.prev = x[x.length - 1];
    return Int16Array.from(out);
  }
}

export const CHUNK = 1920; // 80 ms at 24 kHz

const Base = globalThis.AudioWorkletProcessor ?? class {};

class Capture extends Base {
  constructor() {
    super();
    this.rs = new Resampler(globalThis.sampleRate);
    this.buf = new Int16Array(CHUNK);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch) {
      for (const s of this.rs.push(ch)) {
        this.buf[this.n++] = s;
        if (this.n === CHUNK) {
          this.port.postMessage(this.buf.buffer, [this.buf.buffer]);
          this.buf = new Int16Array(CHUNK);
          this.n = 0;
        }
      }
    }
    return true;
  }
}

if (typeof globalThis.registerProcessor === 'function') globalThis.registerProcessor('fa-capture', Capture);
