// node --test tests/*.test.mjs — the coach's voice choice, the push-to-talk WAV, the worklet's resampler.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pickVoice } from '../web/js/speech.js';
import { wav } from '../web/js/voice.js';
import { Resampler } from '../web/js/voice-worklet.js';

test('the most natural English voice wins; novelty and non-English voices never', () => {
  const v = (name, lang, localService) => ({ name, lang, localService, default: false });
  const samantha = v('Samantha', 'en-US', true), google = v('Google US English', 'en-US', false);
  const ava = v('Ava (Premium)', 'en-US', true), edge = v('Microsoft Ava Online (Natural) - English (United States)', 'en-US', false);
  const bubbles = v('Bubbles', 'en-US', true), french = v('Thomas', 'fr-FR', true);
  assert.equal(pickVoice([samantha, google, ava, edge]), ava);      // installed premium first (on-device)
  assert.equal(pickVoice([samantha, google, edge]), edge);          // then online neural
  assert.equal(pickVoice([samantha, google]), google);
  assert.equal(pickVoice([bubbles, samantha]), samantha);          // never a novelty voice
  assert.equal(pickVoice([bubbles, french]), null);                // nothing usable → captions only
  assert.equal(pickVoice([]), null);
  assert.equal(pickVoice(undefined), null);
});

test('push-to-talk WAV: RIFF, PCM, mono, 16-bit, 24 kHz, little-endian samples', () => {
  const bytes = wav([Int16Array.from([1, -2]), Int16Array.from([32767])]);
  const v = new DataView(bytes.buffer);
  const s = (o, n) => String.fromCharCode(...bytes.slice(o, o + n));
  assert.equal(s(0, 4), 'RIFF'); assert.equal(s(8, 4), 'WAVE'); assert.equal(s(36, 4), 'data');
  assert.equal(v.getUint16(20, true), 1); assert.equal(v.getUint16(22, true), 1);
  assert.equal(v.getUint32(24, true), 24000); assert.equal(v.getUint16(34, true), 16);
  assert.equal(v.getUint32(40, true), 6); assert.equal(bytes.length, 50);
  assert.deepEqual([v.getInt16(44, true), v.getInt16(46, true), v.getInt16(48, true)], [1, -2, 32767]);
});

test('resampler: 48 kHz → 24 kHz halves the samples across buffers, keeps the signal, clips to 16 bits', () => {
  const r = new Resampler(48000);
  let n = 0;
  for (let i = 0; i < 100; i++) n += r.push(new Float32Array(128).fill(0.5)).length; // 128-frame render quanta
  assert.equal(n, 6400);
  assert.ok(r.push(new Float32Array(128).fill(0.5)).every((x) => x === Math.round(0.5 * 0x7fff)));
  assert.ok(new Resampler(24000).push(Float32Array.from([2, -2])).every((x) => Math.abs(x) === 0x7fff));
  const up = new Resampler(44100); // 44.1 kHz input: 24000/44100 of the samples, ±1
  let m = 0;
  for (let i = 0; i < 441; i++) m += up.push(new Float32Array(100)).length;
  assert.ok(Math.abs(m - 24000) <= 1, `${m}`);
});

test('a line that has started is never cut off; waiting lines about another step are dropped', async () => {
  const spoken = [];
  let cancelled = 0;
  globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  globalThis.speechSynthesis = {
    getVoices: () => [{ name: 'Samantha', lang: 'en-US', localService: true }],
    speak: (u) => spoken.push(u),
    cancel: () => { cancelled += 1; },
  };
  const { speak, speaking } = await import('../web/js/speech.js?queue');
  speak("Yes, that's right! Next up, step 3: strip the wire.", 3);
  speak('Your turn. Strip about one centimetre.', 4);  // step 3's second sentence was waiting: stale, dropped
  assert.equal(cancelled, 0);                          // nothing in progress is ever cancelled
  assert.deepEqual(spoken.map((u) => u.text), ["Yes, that's right!"]);
  spoken[0].onend();
  spoken[1].onend();
  assert.deepEqual(spoken.map((u) => u.text), ["Yes, that's right!", 'Your turn.', 'Strip about one centimetre.']);
  spoken[2].onend();
  assert.equal(speaking(), false);
});

test('barge-in: our own words coming back are echo; the learner talking is not', async () => {
  globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  const spoken = [];
  globalThis.speechSynthesis = { getVoices: () => [{ name: 'Samantha', lang: 'en-US', localService: true }],
    speak: (u) => spoken.push(u), cancel: () => {} };
  const { speak, isEcho, hush, speaking } = await import('../web/js/speech.js?echo');
  speak('Next up, step three: strip the red wire.');                 // one sentence, playing now
  assert.equal(isEcho('next up step three strip the'), true);       // the speakers, picked up by the mic
  assert.equal(isEcho('wait which one do I use'), false);           // the learner, talking over the coach
  hush();
  assert.equal(speaking(), false);
  assert.equal(isEcho('next'), true);                               // just after we stopped: still possibly echo
});

test('the coach speaks the learner language: a Spanish learner gets a Spanish voice, never an English one', () => {
  const es = { name: 'Paulina', lang: 'es-MX', localService: true };
  const en = { name: 'Samantha', lang: 'en-US', localService: true };
  assert.equal(pickVoice([en, es], 'es'), es);
  assert.equal(pickVoice([en], 'es'), null);
  assert.equal(pickVoice([en, es], 'en'), en);
});
