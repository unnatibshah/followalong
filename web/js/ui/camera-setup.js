// S6 Camera setup: one line ("Show me your hands and what you're working with"), then the coach looks through the
// camera every couple of seconds and starts the lesson by itself as soon as it can see the work area. Start skips it.
import { h, icon } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { hasCamera, cameraView, grabFrame } from '../camera.js';
import { thermostatScene } from '../thermostatscene.js';
import { askMic } from '../voice.js';
import { nav } from '../router.js';
import { speak, hush, whenQuiet } from '../speech.js';

let micBlocked = false;
export const devBlockMic = () => { micBlocked = true; };

export async function render(root) {
  if (!app.sessionId || app.mode !== 'live') { nav('consent'); return null; }
  const media = hasCamera() ? cameraView() : thermostatScene({ tutorial: app.tutorialId, variant: 'before', surface: 'desk' });
  const INTRO = 'Show me your hands and what you\u2019re working with.';
  const found = h('div', { class: 'seen', role: 'status' }, icon('spin', 'spin sam'), h('span', {}, INTRO));
  const micNote = h('div', { class: 'note warn-dark', hidden: true }, 'Voice is off. Buttons and shortcuts still work.');
  const sw = h('button', { class: 'switch-row', role: 'switch', 'aria-checked': String(!!app.voiceOn) },
    h('span', {}, 'Hands-free voice coach ', h('span', { class: 'dim normal' }, '· recommended: ask "am I doing it right?"')), h('span', { class: 'switch teal', 'aria-hidden': 'true' }));
  // The switch, with the copy next to it, is the microphone consent (FR-92): no getUserMedia for audio before it.
  // Turning it on asks for the microphone once and releases it; S7 opens it only while listening.
  sw.addEventListener('click', async () => {
    micNote.hidden = true;
    if (app.voiceOn) app.voiceOn = false;
    else {
      try {
        if (micBlocked) { micBlocked = false; throw new DOMException('Simulated', 'NotAllowedError'); }
        await askMic();
        app.voiceOn = true;
      } catch { micNote.hidden = false; app.voiceOn = false; }
    }
    sw.setAttribute('aria-checked', String(app.voiceOn));
  });
  // Hands-free by default: the first time, the switch turns itself on (the browser still asks for the microphone)
  if (app.voiceOn === null) sw.click();
  const corner = (c) => h('span', { class: `corner ${c}` });

  root.append(h('div', { class: 'wrap-wide' },
    h('h1', { class: 'title-serif' }, 'Show me your work area'),
    h('p', { class: 'dim setup-sub' }, 'Point the camera at your hands and the things you\u2019ll use, looking down if you can. We start as soon as I can see them.'),
    h('div', { class: 'split-cam' },
      h('div', { class: 'media-frame' }, media,
        h('div', { class: 'work-area' }, corner('tl'), corner('tr'), corner('bl'), corner('br'), h('span', { class: 'pill bl' }, 'Work area')),
        h('span', { class: 'pill tl' }, h('span', { class: 'rec-dot', 'aria-hidden': 'true' }), hasCamera() ? 'Live preview' : 'Stand-in preview (no camera)'),
        found),
      h('div', { class: 'col gap-m' },
        app.config.voice !== 'off' && h('div', { class: 'card-dark col gap-s' }, sw,
          h('div', { class: 'small dim' }, 'Voice lets you say "next", "again" or ask a question. While the microphone icon shows "Listening", '
            + "your speech goes to Meta's transcription model. We don't keep recordings."),
          // A new tab: leaving S6 would redo its camera check on the way back.
          h('a', { href: '#/privacy', target: '_blank', rel: 'noopener', class: 'btn-text accent start' }, 'Read the full privacy notice'), micNote),
        h('button', { class: 'btn btn-accent btn-lg', onclick: () => nav('follow') }, 'Start now →'),
        h('a', { href: '#/replay', class: 'btn-text accent center' }, 'No webcam? Watch a replay')))));

  // The coach checks the framing: a look every ~2.5 s; the first good one starts the lesson. A "can't see" line is
  // shown each time but said at most every 10 s, so it never nags.
  const video = media instanceof SVGElement ? media : root.querySelector('video');
  let spokenAt = 0;
  const say = (text, force = false) => {
    found.replaceChildren(h('span', {}, text));
    if (app.voiceOn !== false && (force || Date.now() - spokenAt > 10000)) { spokenAt = Date.now(); speak(text, 'setup'); }
  };
  let lastSaid = INTRO, stopped = false, looks = 0;
  const started = Date.now();
  async function look() {
    if (stopped || !root.isConnected) return;
    if (document.hidden) { setTimeout(look, 2500); return; } // no looks (and no API calls) in a background tab
    if (++looks > 24) { // about a minute without a clear view: stop asking the model, the button still starts
      found.replaceChildren(h('span', {}, 'Start when you\u2019re ready. I\u2019ll tell you during the lesson if I can\u2019t see.'));
      return;
    }
    try {
      const frame = await grabFrame(video);
      const res = await api.watch(app.sessionId, frame, { step_id: 1, recent: [], said: [lastSaid], elapsed_s: Math.round((Date.now() - started) / 1000), encourage: false });
      if (stopped || !root.isConnected) return;
      if (res.status === 'busy') { setTimeout(look, 2500); return; }
      if (res.status === 'cant_see') {
        const line = res.say || 'I can\u2019t see your work area yet. Point the camera at your hands and the table.';
        if (line !== lastSaid) { lastSaid = line; say(line); }
      } else {
        if (app.voiceOn === null && app.config.voice !== 'off') { // the microphone question is still open: starting now would leave the coach silent
          found.replaceChildren(h('span', {}, 'Looks good. Answer the microphone question and we\u2019ll start.'));
          setTimeout(look, 1500);
          return;
        }
        stopped = true;
        say('Perfect, I can see everything. Let\u2019s start.', true);
        await Promise.all([whenQuiet(5000), new Promise((r) => setTimeout(r, 1800))]); // let the line finish
        if (root.isConnected) nav('follow');
        return;
      }
    } catch (err) {
      found.replaceChildren(h('span', { class: 'miss' }, err.message));
      return; // the button still starts the lesson
    }
    setTimeout(look, 2500);
  }
  if (app.voiceOn !== false) setTimeout(() => { if (!stopped && root.isConnected) say(INTRO, true); }, 400);
  if (hasCamera()) setTimeout(look, 1500); // let the camera settle first (a stand-in picture is never sent: it can't pass)
  else found.replaceChildren(h('span', {}, 'No camera found. Press Start now to follow with a stand-in picture.'));
  return () => { stopped = true; hush(); };
}
