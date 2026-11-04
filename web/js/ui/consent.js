// S5 Age and consent. The box gates the button; POST /api/sessions sends age_confirmed; then the camera turns on.
// Wording: documentation/08-legal/02-consent-screens.md §1.
import { h } from './dom.js';
import { api } from '../api.js';
import { app, newSession } from '../state.js';
import { startCamera } from '../camera.js';
import { askMic } from '../voice.js';
import { nav } from '../router.js';
import { showError, toast } from '../overlays.js';
import { setVoiceLang, unlockSpeech } from '../speech.js';
import { progressOf, clearProgress } from '../progress.js';

// The coach's languages (server/i18n.py LANGS): it speaks and listens in this one; screen labels stay English.
export const LANGS = { en: 'English', es: 'Español', fr: 'Français', de: 'Deutsch', pt: 'Português', it: 'Italiano', hi: 'हिन्दी' };

export async function render(root) {
  if (!app.tutorialId) { nav(''); return null; } // nothing chosen in this tab: back to S1
  app.mode = 'live';
  const meta = await api.tutorial(app.tutorialId).catch(() => ({}));
  const risk = meta.safety?.level === 'gate' || meta.safety?.level === 'danger';
  const saved = progressOf(app.tutorialId);
  // One tap: confirmed once in this browser, the next lesson starts straight away (camera + voice coach, no more screens)
  let remembered = false;
  try { remembered = localStorage.getItem('fa.adult') === '1'; } catch { /* storage blocked */ }
  const box = h('input', { type: 'checkbox', id: 'age', class: 'check', checked: remembered });
  const btn = h('button', { class: 'btn btn-accent btn-lg', disabled: !remembered },
    `${risk ? 'I understand. ' : ''}${saved ? `Continue from step ${saved.step}` : risk ? 'Start' : 'Start: camera and voice coach on'}`);
  // Someone who left mid-lesson picks up where they were, or starts again
  const over = saved && h('button', { class: 'btn btn-outline-dark', type: 'button', onclick: () => { clearProgress(app.tutorialId); app.resumeStep = null; start(1); } },
    'Start from step 1');
  const langSel = h('select', { class: 'lang-select', 'aria-label': 'Coach language' },
    Object.entries(LANGS).map(([code, label]) => h('option', { value: code, selected: code === app.lang }, label)));
  langSel.addEventListener('change', () => { app.lang = langSel.value; });
  // Lessons with electricity, car batteries or moving parts: what the camera can and can't do, before the camera turns on
  const warn = risk && h('div', { class: 'note warn-dark safety-notice', role: 'note' },
    h('strong', {}, '⚠️ This lesson can hurt you if a step goes wrong'),
    h('span', {}, `It involves ${meta.safety.electric ? 'electricity' : 'hazards like moving parts or hot surfaces'}. `
      + "FollowAlong sees your camera picture, not electricity: it can't tell whether power is off or a wire is live. "
      + "At each safety step it stops until you confirm you've done it, and it never moves on by itself after a risky step. "
      + "Use a voltage tester, follow local codes, and if you're unsure, stop and call a licensed professional."));
  const hint = h('span', { class: 'hint', id: 'age-hint' }, "Tick the box to confirm you're 18 or older.");
  btn.setAttribute('aria-describedby', 'age-hint');
  const note = h('div', { class: 'note bad-dark row', hidden: true });
  const showNote = (text) => {
    note.replaceChildren(h('span', {}, text), h('a', { href: '#/replay', class: 'btn btn-light btn-sm' }, 'Watch a recorded session'));
    note.hidden = false;
  };
  const label = h('label', { class: 'age-box', for: 'age' }, box, "I'm 18 or older.");
  if (over) over.disabled = !remembered;
  box.addEventListener('change', () => { btn.disabled = !box.checked; if (over) over.disabled = !box.checked; hint.hidden = box.checked; label.classList.toggle('on', box.checked); });

  btn.addEventListener('click', () => start(saved?.step || 1));
  async function start(step) {
    note.hidden = true;
    btn.disabled = true;
    unlockSpeech(); // inside the tap: iOS only lets speech start from one
    setVoiceLang(app.lang);
    app.resumeStep = step > 1 ? step : null;
    try {
      const { session_id: sid } = await api.startSession({ tutorial_id: app.tutorialId, mode: 'live', age_confirmed: box.checked,
        lang: app.lang });
      newSession(sid, 'live');
    } catch (err) {
      btn.disabled = !box.checked;
      if (err.code === 'limit_reached') return showNote(err.message);
      if (err.code === 'consent_required') { hint.hidden = false; hint.textContent = err.message; return null; }
      return showError(err);
    }
    try {
      await startCamera();
    } catch (err) {
      if (err.name === 'NotAllowedError') { btn.disabled = !box.checked; return showNote('Your browser blocked the camera. Allow it in the address bar, or watch a replay.'); }
      toast('No camera found. Showing a stand-in picture instead.');
      app.voiceOn = false; // one message at a time: the camera one matters more
    }
    try { localStorage.setItem('fa.adult', '1'); } catch { /* storage blocked: ask again next time */ }
    // The voice coach is on unless turned off before; the button on the lesson screen switches it any time
    if (app.config.voice !== 'off' && app.voiceOn !== false) {
      try { await askMic(); app.voiceOn = true; } catch { app.voiceOn = false; toast('Microphone blocked: the coach will still talk; tap Talk to the coach to try again.'); }
    }
    return nav('follow');
  }
  // one tap from the tutorial page, unless there's a safety notice to read or a choice to make (continue or restart)
  if (remembered && !risk && !saved) queueMicrotask(() => btn.click());

  root.append(h('div', { class: 'wrap-narrow col gap-l consent' },
    h('h1', { class: 'title-serif lg' }, remembered && !risk && !saved ? 'Starting your lesson…' : 'Before we start'),
    warn || '',
    saved ? h('p', { class: 'body' }, `You stopped at step ${saved.step} of ${saved.total}.`) : '',
    label,
    h('label', { class: 'lang-row' }, 'Coach speaks ', langSel),
    h('p', { class: 'body' }, "To guide you, FollowAlong sends still pictures from your camera to Meta's AI models every few seconds while "
      + "you work. We don't keep your pictures. Meta doesn't use them to train its models, but may keep them for a time for safety checks. "
      + 'We keep a log of steps and timings, with no pictures. By continuing you agree to the terms of use.'),
    h('p', { class: 'body' }, "The voice coach listens while the 🎙 Listening sign shows: your speech goes to Meta's transcription model, "
      + "and we don't keep recordings. You can turn it off on the lesson screen."),
    h('div', { class: 'links' }, h('a', { href: '#/privacy', class: 'btn-text accent' }, 'Read the full privacy notice'),
      h('a', { href: '#/terms', class: 'btn-text accent' }, 'Terms of use')),
    note,
    h('div', { class: 'col end gap-s' }, h('div', { class: 'row-actions' }, over || '', btn), hint),
    h('a', { href: '#/replay', class: 'btn-text accent start' }, 'Watch a recorded session instead')));
  return null;
}
