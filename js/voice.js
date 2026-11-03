// Voice (TD §2 `voice`, §4.6): microphone → AudioWorklet (24 kHz, 16-bit mono) → our server only.
// Realtime: PCM streams over /ws/voice and the server relays Muse Voice Transcribe's turns back. Push-to-talk (realtime
// unavailable): hold V or the button to record up to 10 s of WAV, posted to /transcribe. The browser's own
// SpeechRecognition is never used (NFR-80). The mic is open only while listening, and audio flows only then.
import { api } from './api.js';
import { speaking, hush, isEcho } from './speech.js';

const RATE = 24000;
const MAX_SAMPLES = RATE * 10;
const MIN_SAMPLES = RATE * 0.3;
const AUDIO = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };

/** A WAV file (RIFF, PCM, mono, 16-bit) from Int16Array chunks. */
export function wav(chunks, rate = RATE) {
  const n = chunks.reduce((s, c) => s + c.length, 0);
  const out = new DataView(new ArrayBuffer(44 + n * 2));
  const str = (o, s) => [...s].forEach((ch, i) => out.setUint8(o + i, ch.charCodeAt(0)));
  str(0, 'RIFF'); out.setUint32(4, 36 + n * 2, true); str(8, 'WAVE');
  str(12, 'fmt '); out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  str(36, 'data'); out.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (const s of c) { out.setInt16(o, s, true); o += 2; }
  return new Uint8Array(out.buffer);
}

/** S6: ask for the microphone once (the switch is the consent), then release it. Throws if blocked. */
export async function askMic() {
  if (!navigator.mediaDevices?.getUserMedia) throw new DOMException('No microphone API', 'NotFoundError');
  const s = await navigator.mediaDevices.getUserMedia({ audio: AUDIO });
  s.getTracks().forEach((t) => t.stop());
}

async function openMic(onChunk) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: AUDIO });
  const ctx = new AudioContext();
  try {
    await ctx.audioWorklet.addModule('/js/voice-worklet.js');
    await ctx.resume();
    const node = new AudioWorkletNode(ctx, 'fa-capture', { numberOfInputs: 1, numberOfOutputs: 0 });
    node.port.onmessage = (e) => onChunk(e.data);
    ctx.createMediaStreamSource(stream).connect(node);
  } catch (err) { stream.getTracks().forEach((t) => t.stop()); ctx.close(); throw err; }
  return { stop: () => { stream.getTracks().forEach((t) => t.stop()); ctx.close(); } };
}

/**
 * on: { listening(bool), mode('realtime'|'push_to_talk'|'off'), caption(text), command(name, text), question(text), notice(msg) }
 * Returns { mode, press(), release(), stop() }; press/release are for push-to-talk.
 */
export function createVoice({ sid, mode, on }) {
  let stopped = false, ws = null, mic = null, rec = null;
  const self = { mode };
  const setMode = (m) => { self.mode = m; on.mode(m); };
  const micDenied = () => { on.notice('Voice is off. Buttons and shortcuts still work.'); setMode('off'); };

  function realtime() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/voice?session_id=${encodeURIComponent(sid)}`);
    ws.binaryType = 'arraybuffer';
    const said = {};
    let failed = null;
    ws.onopen = () => ws.send(JSON.stringify({ type: 'start' }));
    ws.onmessage = async (e) => {
      if (typeof e.data !== 'string') return;
      const m = JSON.parse(e.data);
      if (m.type === 'ready') {
        try {
          mic = await openMic((buf) => {
            // Always the real microphone (echo-cancelled), so the learner can talk over the coach (barge-in).
            if (ws?.readyState === WebSocket.OPEN) ws.send(buf);
          });
          if (stopped) { mic.stop(); mic = null; return; }
          on.listening(true);
        } catch { failed = 'mic'; ws.close(); }
      } else if (m.type === 'caption') {
        said[m.turn_id] = m.text;
        if (isEcho(m.text)) return;                       // our own voice through the speakers
        if (speaking() && m.text.trim().split(/\s+/).length >= 2) hush(); // barge-in: the learner talks, the coach stops
        on.caption(m.text);
      } else if (m.type === 'command') { if (!isEcho(said[m.turn_id] || '')) on.command(m.name, said[m.turn_id] || m.name.replace('_', ' ')); }
      else if (m.type === 'question') { if (!isEcho(m.text)) on.question(m.text); }
      else if (m.type === 'error') failed = m.code;
    };
    ws.onclose = () => {
      mic?.stop(); mic = null; ws = null;
      on.listening(false);
      if (stopped) return;
      if (failed === 'mic') micDenied();
      else if (failed === 'cap_reached') { on.notice('Voice is paused for this session. Buttons and shortcuts still work.'); setMode('off'); }
      else setMode('push_to_talk'); // refused, unavailable or dropped: Hold to talk instead
    };
  }

  self.press = async () => {
    if (self.mode !== 'push_to_talk' || rec || stopped) return;
    const r = rec = { chunks: [], n: 0, released: false, mic: null };
    on.listening(true);
    r.timer = setTimeout(self.release, 10000);
    try {
      r.mic = await openMic((buf) => {
        const take = new Int16Array(buf).subarray(0, MAX_SAMPLES - r.n);
        r.chunks.push(take); r.n += take.length;
        if (r.n >= MAX_SAMPLES) self.release();
      });
    } catch { clearTimeout(r.timer); rec = null; on.listening(false); micDenied(); return; }
    if (r.released) r.mic.stop();
  };

  self.release = async () => {
    const r = rec;
    if (!r || r.released) return;
    r.released = true; rec = null;
    clearTimeout(r.timer);
    r.mic?.stop();
    on.listening(false);
    if (stopped) return;
    if (r.n < MIN_SAMPLES) { on.notice('Hold V (or the button) while you speak.'); return; } // too short to send
    try {
      const res = await api.transcribe(sid, new Blob([wav(r.chunks)], { type: 'audio/wav' }));
      if (stopped) return;
      if (res.command) on.command(res.command, res.transcript);
      else if (res.transcript) on.question(res.transcript);
      else on.notice("Didn't catch that. Hold V and try again.");
    } catch (err) {
      if (err.code === 'cap_reached') { on.notice(err.message); setMode('off'); } else on.notice(err.message);
    }
  };

  self.stop = () => {
    stopped = true;
    if (rec) { clearTimeout(rec.timer); rec.mic?.stop(); rec = null; }
    mic?.stop(); mic = null;
    if (ws) { try { ws.send(JSON.stringify({ type: 'stop' })); } catch { /* closing */ } ws.close(); }
  };

  if (mode === 'realtime') realtime();
  return self;
}
