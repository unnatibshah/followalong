// S1 Start: hero, bundled tutorials and uploads (GET /api/tutorials), upload drop zone (POST /api/tutorials).
import { h, icon, mmss } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { drawsStandIn, stepFrame } from '../thermostatscene.js';
import { nav } from '../router.js';
import { showError, toast } from '../overlays.js';
import { unfinished } from '../progress.js';

export async function render(root) {
  const live = app.config.live_enabled;
  const note = h('div', { class: 'note', hidden: true });
  const showNote = (text, kind = 'warn', link) => {
    note.className = `note ${kind}`;
    note.replaceChildren(h('span', {}, text), link || '');
    note.hidden = false;
  };

  const hero = h('section', { class: 'wrap hero' },
    h('div', { class: 'hero-copy' },
      h('h1', { class: 'display' }, 'Tutorials that wait for you.'),
      h('p', { class: 'lede' }, 'Learn a hands-on skill from a video. It waits for you, checks each step, and replays the part you got wrong.'),
      h('div', { class: 'hero-actions' },
        h('a', { href: '#/replay', class: 'btn btn-accent btn-xl glow' }, 'Watch a replay', icon('arrow')),
        h('button', { class: 'btn-underline', onclick: () => root.querySelector('#fa-lib').scrollIntoView({ behavior: 'smooth' }) }, 'Pick a tutorial ↓')),
      h('div', { class: 'fine' }, 'The replay plays a recorded session. No camera or API key needed.')),
    h('div', { class: 'hero-art' },
      h('a', { href: '#/replay', class: 'hero-frame', 'aria-label': 'Watch a replay' },
        stepFrame('breadboard-led', 3), // real tutorial frames (bundled, CC BY), not drawn
        h('div', { class: 'hero-badge' }, icon('x'), h('span', {}, 'Not quite')),
        h('div', { class: 'hero-inset' }, stepFrame('breadboard-led', 2), h('span', { class: 'inset-tag' }, '3 s clip · loop')),
        h('div', { class: 'hero-hint' }, "The LED doesn't light: it's in backwards. Turn it round so the long leg goes to the resistor.")),
      h('div', { class: 'hero-legend', 'aria-hidden': 'true' },
        ['pauses', 'outlines draw', 'catches the slip', 'loops 3 s', 'tick'].flatMap((t, i) =>
          [i ? h('span', { class: 'rule' }) : null, h('span', { class: i === 2 ? 'slip' : '' }, t)]))));

  const how = h('section', { class: 'wrap' }, h('div', { class: 'how' },
    h('div', { class: 'how-lead' }, 'It waits for you, checks your work, and catches you when you slip.'),
    [['01', 'It studies the video once', 'Muse Spark turns any how-to video into exact steps: which row, which terminal, which way round, and what to be careful of.'],
      ['02', 'It watches you live', 'Every couple of seconds it looks through your camera and tells you, like a teacher would: "Oh, wait, that\'s the wrong row."'],
      ['03', 'You just talk to it', '"Am I doing it right?" "Go back a step." "What\'s this part for?" Hands-free, start to finish.']].map(([n, t, d]) =>
      h('div', { class: 'how-item' }, h('span', { class: 'mono muted' }, n), h('span', { class: 'how-title' }, t), h('span', { class: 'how-text' }, d)))));

  const grid = h('div', { class: 'cards', role: 'group', 'aria-label': 'Tutorials' });
  // CC BY attribution: title, author, source and licence for every bundled video
  const credits = h('details', { class: 'credits', hidden: true }, h('summary', {}, 'Video credits and licences'));
  // Slider controls: a page = as many whole cards as fit; the counter shows which ones are on screen
  const prev = h('button', { class: 'cards-nav', type: 'button', 'aria-label': 'Previous tutorials' }, '‹');
  const next = h('button', { class: 'cards-nav', type: 'button', 'aria-label': 'More tutorials' }, '›');
  const count = h('span', { class: 'cards-count', 'aria-live': 'polite' });
  const bar = h('div', { class: 'cards-bar' }, count, prev, next);
  const step = () => (grid.firstElementChild?.getBoundingClientRect().width || 300) + 24;
  const page = (dir) => grid.scrollBy({ left: dir * Math.max(step(), Math.floor(grid.clientWidth / step()) * step()) });
  prev.addEventListener('click', () => page(-1));
  next.addEventListener('click', () => page(1));
  const sync = () => {
    if (!grid.isConnected) { removeEventListener('resize', sync); return; } // left the page
    const n = grid.children.length;
    const first = Math.round(grid.scrollLeft / step());
    const shown = Math.max(1, Math.floor((grid.clientWidth + 24) / step()));
    const last = Math.min(n, first + shown);
    count.textContent = !n ? '' : last > first + 1 ? `${first + 1}–${last} of ${n}` : `${first + 1} of ${n}`;
    prev.disabled = grid.scrollLeft < 4;
    next.disabled = grid.scrollLeft + grid.clientWidth > grid.scrollWidth - 4;
    bar.hidden = n <= shown;
  };
  grid.addEventListener('scroll', sync, { passive: true });
  addEventListener('resize', sync);
  const input = h('input', { type: 'file', accept: 'video/mp4,video/quicktime,video/webm', class: 'visually-hidden', tabindex: '-1', 'aria-hidden': 'true' });
  const chooseBtn = h('button', { class: 'btn btn-white', disabled: !live }, 'Choose a file');
  // CR-006: a paper manual (PDF or photos of its pages) becomes an instruction video
  const manualInput = h('input', { type: 'file', accept: 'application/pdf,.pdf,image/jpeg,image/png,image/webp', multiple: true,
    class: 'visually-hidden', tabindex: '-1', 'aria-hidden': 'true' });
  const manualBtn = h('button', { class: 'btn btn-white', disabled: !live, type: 'button' }, 'Upload a manual');

  // Paste a link (YouTube / Vimeo), or record yourself doing the task once so others can learn it
  const linkInput = h('input', { type: 'url', class: 'ask-input link-input', placeholder: 'Paste a YouTube or Vimeo link',
    'aria-label': 'Paste a YouTube or Vimeo link', disabled: !live });
  const linkForm = h('form', { class: 'link-form', hidden: !app.config.link_import }, linkInput,
    h('button', { type: 'submit', class: 'btn btn-accent', disabled: !live }, 'Get the steps'));
  linkForm.addEventListener('submit', (e) => { e.preventDefault(); if (linkInput.value.trim()) upload(null, linkInput.value.trim()); });
  const recBtn = h('button', { class: 'btn btn-white', disabled: !live, type: 'button' }, '● Record yourself');
  let recorder = null;
  recBtn.addEventListener('click', async () => {
    if (recorder) { recorder.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 },
        facingMode: { ideal: 'environment' } }, audio: true });
      const chunks = [];
      recorder = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported('video/webm') ? 'video/webm' : '' });
      recorder.ondataavailable = (ev) => ev.data.size && chunks.push(ev.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        recorder = null;
        recBtn.textContent = '● Record yourself';
        upload(new File(chunks, 'my-recording.webm', { type: 'video/webm' }));
      };
      recorder.start(1000);
      recBtn.textContent = '■ Stop and get the steps';
      toast('Recording: do the task once, talking through each step. Tap stop when you finish.');
    } catch { toast('The camera or microphone is blocked. Allow them in the address bar.'); }
  });

  const drop = h('div', { class: ['drop', !live && 'off'] }, input, manualInput,
    h('div', { class: 'drop-icon', 'aria-hidden': 'true' }, icon('upload')),
    h('div', { class: 'drop-text' },
      h('span', { class: 'drop-title' }, `Or upload your own video (MP4, up to ${Math.round(app.config.limits.upload_max_s / 60)} minutes here)`),
      h('span', { class: 'drop-sub' }, "Upload a video you have the rights to. It's sent to Meta's AI model to find the steps. "
        + 'Uploaded videos and their step plans are deleted after 24 hours. By uploading you confirm you\'re 18 or older and agree to ',
      h('a', { href: '#/terms' }, 'the terms'), '.'),
      h('span', { class: 'drop-sub' }, 'Have a paper manual and no video? Upload the PDF or photos of its pages (up to 40 pages) and '
        + "FollowAlong builds a step-by-step lesson from its pictures. Use a manual you have; it's only used to build your lesson.")),
    h('div', { class: 'col gap-s' }, chooseBtn, manualBtn, recBtn));

  const isManual = (f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name) || f.type.startsWith('image/');

  async function upload(file, url, pages) {
    if ((!file && !url && !pages?.length) || !live) return;
    note.hidden = true;
    try {
      const { id, status, duplicate } = pages ? await api.uploadManual(pages) : url ? await api.uploadLink(url) : await api.upload(file);
      app.tutorialId = id;
      app.mode = 'live';
      if (duplicate) toast('You already have this tutorial, so it opened that one. Nothing was processed again.');
      nav(duplicate && status === 'ready' ? 'ready' : 'understanding');
    } catch (err) {
      if (['too_long', 'limit_reached', 'invalid_upload', 'too_many_pages', 'invalid_manual', 'not_licensed'].includes(err.code)) showNote(err.message);
      else showError(err);
    }
  }
  chooseBtn.addEventListener('click', () => input.click());
  drop.addEventListener('click', (e) => { if (live && e.target !== input && !e.target.closest('button, a')) input.click(); });
  input.addEventListener('change', () => upload(input.files[0]));
  manualBtn.addEventListener('click', () => manualInput.click());
  manualInput.addEventListener('change', () => { upload(null, null, [...manualInput.files]); manualInput.value = ''; });
  drop.addEventListener('dragover', (e) => { e.preventDefault(); if (live) drop.classList.add('drag'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('drag');
    const files = [...e.dataTransfer.files];
    if (files.length && files.every(isManual)) upload(null, null, files);
    else upload(files[0]);
  });

  const lib = h('section', { class: 'wrap lib', id: 'fa-lib' },
    h('div', {}, h('h2', { class: 'section-title' }, 'Pick a tutorial'),
      h('div', { class: 'section-sub' }, 'Each one is ready to follow. Muse Spark has already split it into steps.'),
      h('div', { class: 'section-sub muted' }, 'Each one is a Creative Commons (CC BY) video by the creator named on its card, used under that licence. See the credits below.')),
    grid, bar, credits, drop, linkForm, note);

  // Lessons left part-way, in this browser: one tap back to where they were
  const left = live ? unfinished().slice(0, 2) : [];
  const resume = left.length ? h('section', { class: 'wrap resume' }, left.map((p) => h('button', { class: 'resume-card',
    onclick: () => { app.tutorialId = p.id; app.mode = 'live'; nav('consent'); } },
  h('span', { class: 'resume-k' }, 'Continue'), h('span', { class: 'resume-t' }, p.title || 'Your lesson'),
  h('span', { class: 'resume-s mono' }, `step ${p.step} of ${p.total} →`)))) : '';

  root.append(h('div', { class: 'page-light' }, resume, hero, how, lib,
    h('footer', { class: 'footer' }, h('div', { class: 'wrap footer-row' },
      h('span', {}, 'Built during the Meta AI Hackathon with Muse Spark, SAM 3.1 and Muse Voice Transcribe'),
      h('div', { class: 'footer-links' }, h('a', { href: '#/evaluation' }, 'Evaluation'), h('a', { href: '#/creator' }, 'For creators'),
        h('a', { href: '#/privacy' }, 'Privacy'), h('a', { href: '#/terms' }, 'Terms'),
        !document.documentElement.hasAttribute('data-static') && h('a', { href: '#/ops' }, 'Status'),
        !app.config.hosted && !document.documentElement.hasAttribute('data-static') && h('a', { href: '#/annotate' }, 'Help check (annotate)'))))));

  if (!live) showNote(document.documentElement.hasAttribute('data-static')
    ? 'Static demo: replays and checklists only. For live camera checks, run the app from GitHub with a Meta API key.'
    : 'Add a Meta API key to use live mode. Replays work without one.', 'info');

  try {
    const tutorials = await api.tutorials();
    grid.append(...tutorials.map(card));
    const credited = tutorials.filter((t) => t.source_url);
    credits.append(h('ul', {}, credited.map((t) => h('li', {}, `${t.title}: “${t.source_title || t.title}” by ${t.creator}, `,
      h('a', { href: t.source_url, target: '_blank', rel: 'noopener' }, 'source'), `, ${t.licence}. Cut into steps by FollowAlong.`))));
    credits.hidden = !credited.length;
    requestAnimationFrame(sync);
  } catch {
    bar.hidden = true;
    grid.replaceWith(h('div', { class: 'note warn row' }, h('span', {}, '⚠ Something went wrong loading tutorials. Try a replay instead.'),
      h('a', { href: '#/replay', class: 'btn btn-dark' }, 'Watch a replay')));
  }
  return null;
}

function card(t) {
  const ready = t.status === 'ready';
  const open = () => { app.tutorialId = t.id; app.mode = 'live'; nav(ready ? 'ready' : 'understanding'); };
  // The middle step's end frame: real videos often end on a logo card or a cutaway, not the finished work
  const preview = ready && t.step_count && !drawsStandIn(t.id) ? stepFrame(t.id, Math.ceil(t.step_count / 2))
    : h('div', { class: 'paper-preview' }, `${t.title.toLowerCase()} · preview`);
  const tag = t.source === 'manual' ? 'Made from your manual' : t.source === 'upload' ? 'Your upload' : t.creator ? 'YouTube · credited' : 'Bundled';
  return h('a', { href: `#/${ready ? 'ready' : 'understanding'}`, class: 'card', onclick: (e) => { e.preventDefault(); open(); } },
    h('div', { class: 'card-media' }, preview,
      h('span', { class: 'pill tl' }, h('span', { class: 'dot accent', 'aria-hidden': 'true' }), ready ? 'Steps ready' : 'Processing…'),
      h('span', { class: 'pill tr mono' }, t.manual && !t.duration_s ? `${t.manual.pages} p.` : mmss(t.duration_s))),
    h('div', { class: 'card-title-row' }, h('span', { class: 'card-title' }, t.title), h('span', { class: 'card-open' }, 'Open →')),
    h('div', { class: 'card-meta' }, h('span', { class: 'tag' }, tag), t.step_count ? `${t.step_count} steps` : '',
      t.creator ? h('span', { class: 'muted' }, `by ${t.creator}${t.source_url ? ' · CC BY' : ''}`) : ''));
}
