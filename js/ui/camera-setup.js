// S6 Camera setup: the coach looks through the camera every couple of seconds and says what it needs ("move back, I
// can't see the table") until it can see the work area, then says so and starts the lesson by itself.
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
  const found = h('div', { class: 'seen' }, icon('spin', 'spin sam'), h('span', {}, 'Let me see your work area\u2026'));
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
    h('p', { class: 'dim setup-sub' }, 'Point the camera at your hands and the things you\u2019ll use. I\u2019ll tell you when it\u2019s right, then we start.'),
    h('div', { class: 'split-cam' },
      h('div', { class: 'media-frame' }, media,
        h('div', { class: 'work-area' }, corner('tl'), corner('tr'), corner('bl'), corner('br'), h('span', { class: 'pill bl' }, 'Work area')),
        h('span', { class: 'pill tl' }, h('span', { class: 'rec-dot', 'aria-hidden': 'true' }), hasCamera() ? 'Live preview' : 'Stand-in preview (no camera)'),
        found),
      h('div', { class: 'col gap-m' },
        h('div', { class: 'card-dark col gap-s' }, h('div', { class: 'strong' }, 'Tips'),
          ['Looking straight down works best', 'Keep your pieces inside the frame', 'Even light, no strong shadows']
            .map((t) => h('div', { class: 'tip' }, h('span', { class: 'accent', 'aria-hidden': 'true' }, '•'), t))),
        app.config.voice !== 'off' && h('div', { class: 'card-dark col gap-s' }, sw,
          h('div', { class: 'small dim' }, 'Voice lets you say "next", "again" or ask a question. While the microphone icon shows "Listening", '
            + "your speech goes to Meta's transcription model. We don't keep recordings."),
          // A new tab: leaving S6 would redo its camera check on the way back.
          h('a', { href: '#/privacy', target: '_blank', rel: 'noopener', class: 'btn-text accent start' }, 'Read the full privacy notice'), micNote),
        h('button', { class: 'btn btn-accent btn-lg', onclick: () => nav('follow') }, 'Looks good, start →'),
        h('a', { href: '#/replay', class: 'btn-text accent center' }, 'No webcam? Watch a replay')))));

  // The coach checks the framing: a look every ~2.5 s; two good looks in a row and the lesson starts by itself
  const video = media instanceof SVGElement ? media : root.querySelector('video');
  const say = (text) => { found.replaceChildren(h('span', {}, text)); if (app.voiceOn !== false) speak(text, 'setup'); };
  let good = 0, lastSaid = '', stopped = false, looks = 0;
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
        good = 0;
        const line = res.say || 'I can\u2019t see your work area yet. Point the camera at your hands and the table.';
        if (line !== lastSaid) { lastSaid = line; say(line); }
      } else if (++good >= 2 || !hasCamera()) {
        if (app.voiceOn === null && app.config.voice !== 'off') { // the microphone question is still open: starting now would leave the coach silent
          found.replaceChildren(h('span', {}, 'Looks good. Answer the microphone question and we\u2019ll start.'));
          setTimeout(look, 1500);
          return;
        }
        stopped = true;
        say('Perfect, I can see your work area. Let\u2019s start.');
        await Promise.all([whenQuiet(5000), new Promise((r) => setTimeout(r, 1800))]); // let the line finish
        if (root.isConnected) nav('follow');
        return;
      } else found.replaceChildren(icon('spin', 'spin sam'), h('span', {}, 'Looks good so far\u2026'));
    } catch (err) {
      found.replaceChildren(h('span', { class: 'miss' }, err.message));
      return; // the button still starts the lesson
    }
    setTimeout(look, 2500);
  }
  setTimeout(look, 1200); // let the camera settle first
  return () => { stopped = true; hush(); };
}
