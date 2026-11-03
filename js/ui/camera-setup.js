// S6 Camera setup: live preview (or the stand-in), work-area frame, one visibility check of step 1's objects
// (check_no 0: outside the follow-along numbering, so a recorded session's checks match its replay).
import { h, icon } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { hasCamera, cameraView, grabFrame } from '../camera.js';
import { thermostatScene } from '../thermostatscene.js';
import { askMic } from '../voice.js';
import { nav } from '../router.js';

let micBlocked = false;
export const devBlockMic = () => { micBlocked = true; };

export async function render(root) {
  if (!app.sessionId || app.mode !== 'live') { nav('consent'); return null; }
  const media = hasCamera() ? cameraView() : thermostatScene({ tutorial: app.tutorialId, variant: 'before', surface: 'desk' });
  const found = h('div', { class: 'seen' }, icon('spin', 'spin sam'), h('span', {}, 'SAM 3.1 is looking for your pieces…'));
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
    h('h1', { class: 'title-serif' }, 'Point your camera down at your desk'),
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

  try {
    const frame = await grabFrame(media instanceof SVGElement ? media : root.querySelector('video'));
    const res = await api.check(app.sessionId, frame, { step_id: 1, kind: 'visibility', check_no: 0, trigger: 'check_me', trigger_at: Date.now() });
    found.replaceChildren(...res.objects.map((o) => h('span', { class: o.found ? 'ok' : 'miss' }, o.found ? `✓ ${o.name}` : `✗ ${o.name} (can't see)`)));
  } catch (err) {
    found.replaceChildren(h('span', { class: 'miss' }, err.message));
  }
  return null;
}
