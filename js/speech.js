// The coach's voice (TD §2 `speech`, FR-94): the browser's speechSynthesis with the most natural English voice it
// has, sentence by sentence, in a queue: a line that has started is never cut off. Captions are always on, so nothing
// depends on sound. Online voices (Google, Microsoft "Natural") receive the reply text only, never the learner's audio.

// Novelty and robotic macOS voices: never the coach.
const NOVELTY = /\b(albert|bad news|bahh|bells|boing|bubbles|cellos|wobble|fred|good news|jester|junior|organ|superstar|ralph|trinoids|whisper|zarvox|grandma|grandpa|rocko|kathy)\b/i;

let LANG = 'en'; // the learner's language (two letters); the coach's voice speaks it
/** Speak in this language from now on ('en', 'es', 'hi', …). */
export function setVoiceLang(code) { LANG = code || 'en'; }

/** How natural a voice sounds (higher is better); -1 = never use it. */
export function voiceScore(v, lang = LANG) {
  if (!v || !new RegExp(`^${lang}(\\b|[-_])`, 'i').test(v.lang || '') || NOVELTY.test(v.name || '')) return -1;
  const n = v.name || '';
  let s = 0;
  if (/premium|enhanced|siri|neural/i.test(n)) s = 100;                 // installed high-quality voices (on-device)
  else if (/natural/i.test(n) && /microsoft/i.test(n)) s = 90;         // Edge's online neural voices
  else if (/^google (us|uk) english/i.test(n)) s = 80;                 // Chrome's online voices
  else if (/samantha|ava|allison|susan|zoe|evan|nathan|serena|daniel|karen|moira|tessa|rishi/i.test(n)) s = 50;
  else s = v.localService ? 20 : 10;
  if (lang === 'en' && /^en[-_]us/i.test(v.lang)) s += 5;              // the tutorials are American English
  if (v.localService) s += 1;                                          // a tie goes to on-device
  return s;
}

/** The most natural voice in the learner's language, or null (captions only). */
export function pickVoice(voices, lang = LANG) {
  return (voices || []).reduce((best, v) => (voiceScore(v, lang) > voiceScore(best, lang) ? v : best), null);
}

/** iOS Safari speaks only after a tap has started speech once: call this inside the Start tap. */
export function unlockSpeech() {
  const s = synth();
  if (!s || typeof SpeechSynthesisUtterance === 'undefined') return;
  const u = new SpeechSynthesisUtterance(' ');
  u.volume = 0;
  s.speak(u);
}

const synth = () => globalThis.speechSynthesis;
synth()?.getVoices(); // Chrome fills the list asynchronously; ask early

const queue = [];          // sentences waiting: { text, tag }
let lastEnd = 0;           // when the coach last stopped talking
const recent = [];         // what we said lately: { words, at }, to tell the learner's voice from our own echo
let current = null;        // the utterance being spoken, never cancelled by a new line
const listeners = new Set();
const notify = () => listeners.forEach((fn) => fn(speaking()));

/** fn(isSpeaking) whenever the coach starts or stops talking (the follow screen ducks the tutorial video). */
export function onSpeech(fn) { listeners.add(fn); return () => listeners.delete(fn); }

function next() {
  const s = synth();
  const item = queue.shift();
  if (!s || !item) { if (current) lastEnd = Date.now(); current = null; notify(); return; }
  const voice = pickVoice(s.getVoices());
  const u = new SpeechSynthesisUtterance(item.text);
  if (voice) { u.voice = voice; u.lang = voice.lang; }
  u.rate = voice && !voice.localService ? 1.0 : 1.03;  // a touch quicker than the slow default on local voices
  u.pitch = 1.0;
  u.onend = u.onerror = () => { if (current === u) next(); };
  current = u;
  recent.push({ words: words(item.text), at: Date.now() });
  if (recent.length > 6) recent.shift();
  s.speak(u);
  notify();
}

/** Say `text`, after whatever is being said (`urgent`: right now, cutting in). `tag` (e.g. the step number): sentences still waiting with another
 *  tag are stale and dropped; the one being spoken always finishes. False when there's no usable voice. */
export function speak(text, tag = null, urgent = false) {
  const s = synth();
  if (!s || !text || !pickVoice(s.getVoices())) return false;
  if (urgent) { queue.length = 0; current = null; s.cancel(); } // "Oh, wait": a mistake cuts in, like a person would
  if (tag !== null) for (let i = queue.length - 1; i >= 0; i--) if (queue[i].tag !== null && queue[i].tag !== tag) queue.splice(i, 1);
  for (const sentence of text.match(/[^.!?]+[.!?]*\s*/g) || [text]) if (sentence.trim()) queue.push({ text: sentence.trim(), tag });
  if (!current) next();
  return true;
}

export const speaking = () => !!current || queue.length > 0;

const words = (t) => (t.toLowerCase().match(/[\p{L}\p{N}']+/gu) || []);
/** True when `heard` is (mostly) our own voice coming back through the speakers: most of its words are in what we
 *  said in the last 10 s. The browser's echo cancellation removes most of it; this catches the rest. */
export function isEcho(heard) {
  if (!speaking() && Date.now() - lastEnd > 1500) return false; // echo only while (or just after) we talk
  const w = words(heard);
  if (!w.length) return true;
  const ours = new Set(recent.filter((r) => Date.now() - r.at < 10000).flatMap((r) => r.words));
  return w.filter((x) => ours.has(x)).length / w.length >= 0.6;
}

/** Stop now and forget the queue (the session ended, the learner turned voice off). */
export const hush = () => { if (current) lastEnd = Date.now(); queue.length = 0; current = null; synth()?.cancel(); notify(); };
