// S3 Ready: title, step count, video length and the "you'll need" list from the StepPlan.
import { h } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { stepFrame } from '../thermostatscene.js';
import { nav } from '../router.js';
import { showError, toast } from '../overlays.js';

async function share(meta) {
  const url = `${location.origin}${location.pathname}#/ready?t=${encodeURIComponent(meta.id)}`;
  const text = `Follow along: ${meta.title}. It watches through your camera and tells you when a step's right.`;
  try {
    if (navigator.share) await navigator.share({ title: meta.title, text, url });
    else { await navigator.clipboard.writeText(url); toast('Link copied.'); }
  } catch (err) {
    if (err?.name !== 'AbortError') toast(`Copy this link to share it: ${url}`); // no share sheet and no clipboard
  }
}

export async function render(root) {
  if (!app.tutorialId) { nav(''); return null; } // nothing chosen in this tab: back to S1
  app.mode = 'live';
  let meta, plan;
  try { [meta, plan] = await Promise.all([api.tutorial(app.tutorialId), api.plan(app.tutorialId)]); } catch (err) { showError(err); return null; }
  const live = app.config.live_enabled;
  const s = Math.round(meta.duration_s);
  const length = s < 90 ? `${s} seconds of video` : `about ${Math.round(s / 60)} minutes of video`;
  root.append(h('div', { class: 'wrap-mid split' },
    h('div', { class: 'media-frame' }, stepFrame(app.tutorialId, plan.steps.length, { variant: 'end', surface: 'studio', tags: false }),
      h('span', { class: 'pill tl' }, meta.source === 'manual' ? 'Last step · from your manual' : 'Finished · end of the video')),
    h('div', { class: 'col gap-l' },
      h('a', { class: 'btn-text back-link', href: '#/library' }, '← All guides'),
      h('div', { class: 'eyebrow' }, 'Ready to follow along'),
      h('h1', { class: 'title-serif lg' }, meta.title),
      h('div', { class: 'muted' }, meta.source === 'manual'
        ? `${plan.steps.length} steps · made from your ${meta.manual.pages}-page manual`
        : `${plan.steps.length} steps · ${length}`),
      meta.source === 'manual' && h('div', { class: 'muted' }, 'Each step is an animated video made from the picture in your manual, with its page number. If anything looks different, check the manual.'),
      h('div', { class: 'col gap-s' }, h('span', { class: 'strong' }, "You'll need:"),
        h('div', { class: 'needs' }, plan.needs.map((n) => h('span', { class: 'need' }, n)))),
      !live && h('div', { class: 'note info-dark' }, 'Live camera coaching is switched off here. ',
        h('a', { href: '#/replay' }, 'Watch a recorded session →')),
      h('div', { class: 'row-between' },
        h('button', { class: 'btn btn-outline-dark', onclick: () => { app.reviewStep = 1; nav('review'); } },
          'Review the checks ', h('span', { class: 'dim' }, '(optional)')),
        h('button', { class: 'btn btn-outline-dark', onclick: () => nav('checklist') }, 'Checklist (no camera)'),
        !['upload', 'manual'].includes(meta.source) && h('button', { class: 'btn btn-outline-dark', onclick: () => share(meta) }, 'Share'),
        h('button', { class: 'btn btn-accent btn-lg', disabled: !live, onclick: () => nav('consent') }, 'Start following along →')))));
  return null;
}
