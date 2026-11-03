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
  } catch { /* closed the share sheet */ }
}

export async function render(root) {
  if (!app.tutorialId) { nav(''); return null; } // nothing chosen in this tab: back to S1
  app.mode = 'live';
  let meta, plan;
  try { [meta, plan] = await Promise.all([api.tutorial(app.tutorialId), api.plan(app.tutorialId)]); } catch (err) { showError(err); return null; }
  const live = app.config.live_enabled;
  const minutes = Math.max(1, Math.round(meta.duration_s / 60));
  root.append(h('div', { class: 'wrap-mid split' },
    h('div', { class: 'media-frame' }, stepFrame(app.tutorialId, plan.steps.length, { variant: 'end', surface: 'studio', tags: false }),
      h('span', { class: 'pill tl' }, meta.source === 'manual' ? 'Last step · from your manual' : 'Finished · end of the video')),
    h('div', { class: 'col gap-l' },
      h('div', { class: 'eyebrow' }, 'Ready to follow along'),
      h('h1', { class: 'title-serif lg' }, meta.source === 'manual'
        ? `${meta.title} · ${plan.steps.length} steps · made from your ${meta.manual.pages}-page manual`
        : `${meta.title} · ${plan.steps.length} steps · about ${minutes} minute${minutes === 1 ? '' : 's'} of video`),
      meta.source === 'manual' && h('div', { class: 'muted' }, 'Each step is an animated video made from the picture in your manual, with its page number. If anything looks different, check the manual.'),
      h('div', { class: 'col gap-s' }, h('span', { class: 'strong' }, "You'll need:"),
        h('div', { class: 'needs' }, plan.needs.map((n) => h('span', { class: 'need' }, n)))),
      !live && h('div', { class: 'note info-dark' }, 'Add a Meta API key to use live mode. Replays work without one. ',
        h('a', { href: '#/replay' }, 'Watch a replay →')),
      h('div', { class: 'row-between' },
        h('button', { class: 'btn btn-outline-dark', onclick: () => { app.reviewStep = 1; nav('review'); } },
          'Review the checks ', h('span', { class: 'dim' }, '(optional)')),
        h('button', { class: 'btn btn-outline-dark', onclick: () => nav('checklist') }, 'Checklist (no camera)'),
        !['upload', 'manual'].includes(meta.source) && h('button', { class: 'btn btn-outline-dark', onclick: () => share(meta) }, 'Share'),
        h('button', { class: 'btn btn-accent btn-lg', disabled: !live, onclick: () => nav('consent') }, 'Start following along →')))));
  return null;
}
