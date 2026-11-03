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
  // A breath between sentences, a longer one after a question or an exclamation, like a person talking
  const gap = /[?!]$/.test(item.text) ? 450 : 250;
  u.onend = u.onerror = () => { if (current === u) setTimeout(() => { if (current === u) next(); }, gap); };
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
  if (!s || !text) return false;
  // Chrome fills its voice list a moment after the page opens: a line said before that waits for it instead of being
  // lost. Once the list is in, a language with no voice at all stays silent (captions only).
  const loading = !s.getVoices().length;
  if (!loading && !pickVoice(s.getVoices())) return false;
  if (urgent) { queue.length = 0; current = null; s.cancel(); } // "Oh, wait": a mistake cuts in, like a person would
  if (tag !== null) for (let i = queue.length - 1; i >= 0; i--) if (queue[i].tag !== null && queue[i].tag !== tag) queue.splice(i, 1);
  for (const sentence of speakable(text).match(/[^.!?]+[.!?]*\s*/g) || [text]) {
    for (const part of breathe(sentence.trim())) if (part) queue.push({ text: part, tag });
  }
  if (loading) { waitForVoices(s); return true; }
  if (!current) next();
  return true;
}

let waiting = false;
function waitForVoices(s) {
  if (waiting) return;
  waiting = true;
  const go = () => { waiting = false; if (!current && pickVoice(s.getVoices())) next(); else if (!pickVoice(s.getVoices())) queue.length = 0; };
  s.addEventListener?.('voiceschanged', go, { once: true });
  setTimeout(() => { if (waiting) go(); }, 1500); // Safari and some Chromes never fire it: try anyway
}

export const speaking = () => !!current || queue.length > 0;

/** Resolves when the coach has finished talking (at once if it isn't), or after `maxMs` whatever happens. */
export function whenQuiet(maxMs = 6000) {
  return new Promise((done) => {
    if (!speaking()) { done(); return; }
    let off = () => {};
    const timer = setTimeout(() => { off(); done(); }, maxMs);
    off = onSpeech((talking) => { if (!talking) { clearTimeout(timer); off(); done(); } });
  });
}

/** Text written for the screen, shaped for a speech engine: no "In the video:" asides (those are for reading), no
 *  arrows, brackets or semicolons, which voices either skip or read as one breathless run. */
export function speakable(text) {
  return ` ${text || ''} `
    .replace(/\s*In the video:[^.!?]*[.!?]?/gi, ' ')
    .replace(/\s*[→⟶]\s*/g, ', to ')
    .replace(/\s*\(([^)]*)\)/g, ', $1,')
    .replace(/\s*;\s*/g, '. ')
    .replace(/(^|[\s,])\+(?=[\s,.]|$)/g, '$1plus')
    .replace(/(^|[\s,])[−–](?=[\s,.]|$)/g, '$1minus')
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.!?:])/g, '$1')
    .replace(/,+([.!?:])/g, '$1')
    .replace(/,\s*,/g, ',')
    .replace(/([.!?]\s+)(\p{Ll})/gu, (_, p, c) => p + c.toUpperCase())
    .trim();
}

/** A long sentence split at its natural joins (", and", ", so", ", then", ", but"), so each part gets its own rise
 *  and fall instead of one flat 40-word line. ponytail: word-count heuristic; the prompt asks for short sentences. */
export function breathe(sentence, max = 16) {
  const n = (t) => t.split(/\s+/).length;
  if (n(sentence) <= max) return [sentence];
  const m = sentence.match(/^(.{20,}?),\s+(and|so|then|but)\s+(.+)$/i);
  if (!m || n(m[1]) < 5) return [sentence];
  const tail = m[3].charAt(0).toUpperCase() + m[3].slice(1);
  return [`${m[1]}.`, ...breathe(m[2].toLowerCase() === 'and' ? tail : `${m[2].charAt(0).toUpperCase() + m[2].slice(1)} ${m[3]}`, max)];
}

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
