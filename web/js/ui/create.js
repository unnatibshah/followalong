// ＋ New guide: every way to start, in one sheet (a link, a video, a manual, snapped pages, a recording). Opened from
// the tab bar, Home and Library. Uploads go to POST /api/tutorials exactly as before; only the place changed.
import { h, icon } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { nav } from '../router.js';
import { closeAll, showError, showSheet, toast } from '../overlays.js';

const MAX_SNAPS = 20; // server/manual.py MAX_IMAGES
const INLINE = ['too_long', 'limit_reached', 'invalid_upload', 'too_many_pages', 'invalid_manual', 'not_licensed'];

// A phone photo is ~3–12 MB; the server keeps 2000 px (manual.PAGE_PX) and refuses > 10 MB, so send that size as JPEG
async function shrink(f, n) {
  const bmp = await createImageBitmap(f); // applies the photo's EXIF orientation
  const k = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
  const c = Object.assign(document.createElement('canvas'), { width: Math.round(bmp.width * k), height: Math.round(bmp.height * k) });
  c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
  bmp.close();
  const blob = await new Promise((ok, bad) => c.toBlob((b) => (b ? ok(b) : bad(new Error('encode'))), 'image/jpeg', 0.85));
  return new File([blob], `page-${String(n).padStart(2, '0')}.jpg`, { type: 'image/jpeg' });
}

const isManual = (f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name) || f.type.startsWith('image/');

export function openCreate() {
  const live = app.config.live_enabled;
  const note = h('div', { class: 'note warn', hidden: true, role: 'status' });
  const say = (text) => { note.textContent = text; note.hidden = false; };
  const body = h('div', { class: 'sheet-body' });
  const busy = h('div', { class: 'sheet-busy', hidden: true }, icon('spin', 'spin'), h('span', {}, 'Sending…'));

  const hidden = (accept, more = {}) => h('input', { type: 'file', accept, class: 'visually-hidden', tabindex: '-1', 'aria-hidden': 'true', ...more });
  const videoIn = hidden('video/mp4,video/quicktime,video/webm');
  const manualIn = hidden('application/pdf,.pdf,image/jpeg,image/png,image/webp', { multiple: true });
  const snapIn = hidden('image/*', { capture: 'environment' });

  async function upload(file, url, pages) {
    if ((!file && !url && !pages?.length) || !live) return;
    note.hidden = true;
    busy.hidden = false;
    try {
      const { id, status, duplicate } = pages ? await api.uploadManual(pages) : url ? await api.uploadLink(url) : await api.upload(file);
      closeAll();
      app.tutorialId = id;
      app.mode = 'live';
      if (duplicate) toast('You already have this guide, so it opened that one. Nothing was processed again.');
      nav(duplicate && status === 'ready' ? 'ready' : 'understanding');
    } catch (err) {
      busy.hidden = true;
      if (INLINE.includes(err.code)) say(err.message);
      else { closeAll(); showError(err); }
    }
  }

  // ---- the menu: one row per way to start ----
  const option = (ic, title, sub, onclick, show = true) => show && h('button', { class: 'opt', type: 'button', disabled: !live, onclick },
    h('span', { class: 'opt-icon', 'aria-hidden': 'true' }, icon(ic)),
    h('span', { class: 'opt-text' }, h('span', { class: 'opt-title' }, title), h('span', { class: 'opt-sub' }, sub)),
    icon('chevron', 'opt-go'));
  function menu() {
    body.replaceChildren(h('div', { class: 'opts' },
      option('link', 'Paste a link', 'A Creative Commons YouTube or Vimeo video', linkView, !!app.config.link_import),
      option('video', 'Upload a video', `MP4, up to ${Math.round(app.config.limits.upload_max_s / 60)} minutes`, () => videoIn.click()),
      option('file', 'Upload a manual', 'A PDF, up to 40 pages', () => manualIn.click()),
      option('camera', 'Snap the pages', 'Photograph a paper manual, page by page', () => snapIn.click()),
      option('record', 'Record yourself', 'Do the task once, talking it through', record)));
  }

  // ---- paste a link ----
  function linkView() {
    const input = h('input', { type: 'url', class: 'field', placeholder: 'https://www.youtube.com/watch?v=…', 'aria-label': 'Video link', required: true });
    const form = h('form', { class: 'col gap-s' },
      h('label', { class: 'field-label' }, 'Paste a Creative Commons YouTube or Vimeo link'), input,
      h('div', { class: 'row gap-s' }, h('button', { type: 'button', class: 'btn-text', onclick: menu }, '← Back'),
        h('button', { type: 'submit', class: 'btn btn-accent' }, 'Get the steps')));
    form.addEventListener('submit', (e) => { e.preventDefault(); if (input.value.trim()) upload(null, input.value.trim()); });
    body.replaceChildren(form);
    input.focus();
  }

  // ---- snap the pages: photograph a manual page by page, check the shots, then build ----
  const snaps = []; // { file, url }
  const dropSnaps = () => snaps.splice(0).forEach((x) => URL.revokeObjectURL(x.url));
  function snapView() {
    const list = h('ol', { class: 'snap-list', 'aria-label': 'Your pages, in order' }, snaps.map((x, i) => h('li', { class: 'snap-page' },
      h('img', { src: x.url, alt: `Page ${i + 1}` }), h('span', { class: 'snap-no mono' }, `p. ${i + 1}`),
      i > 0 && h('button', { class: 'snap-btn snap-move', type: 'button', 'aria-label': `Move page ${i + 1} earlier`,
        onclick: () => { snaps.splice(i - 1, 0, ...snaps.splice(i, 1)); snapView(); } }, '←'),
      h('button', { class: 'snap-btn', type: 'button', 'aria-label': `Remove page ${i + 1}`,
        onclick: () => { URL.revokeObjectURL(snaps.splice(i, 1)[0].url); if (snaps.length) snapView(); else menu(); } }, '×'))));
    body.replaceChildren(h('div', { class: 'col gap-m' },
      h('div', { class: 'muted' }, 'Hold the phone over one page at a time, flat and in good light.'), list,
      h('div', { class: 'row gap-s wrap-row' },
        h('button', { class: 'btn btn-white', type: 'button', disabled: snaps.length >= MAX_SNAPS, onclick: () => snapIn.click() }, icon('plus'), 'Add page'),
        h('button', { class: 'btn btn-accent', type: 'button', onclick: () => upload(null, null, snaps.map((x) => x.file)) },
          `Build my guide (${snaps.length} page${snaps.length === 1 ? '' : 's'})`),
        h('button', { class: 'btn-text', type: 'button', onclick: () => { dropSnaps(); menu(); } }, 'Start over'))));
  }
  snapIn.addEventListener('change', async () => {
    const f = snapIn.files[0];
    snapIn.value = '';
    if (!f) return;
    try {
      const file = await shrink(f, snaps.length + 1);
      snaps.push({ file, url: URL.createObjectURL(file) });
      snapView();
    } catch { toast("That photo couldn't be read. Try again."); }
  });

  // ---- record yourself: do the task once, talking it through, so others can learn it ----
  let recorder = null;
  async function record() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 },
        facingMode: { ideal: 'environment' } }, audio: true });
      const chunks = [];
      recorder = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported('video/webm') ? 'video/webm' : '' });
      let dropped = false; // the sheet was closed mid-recording: turn the camera off, upload nothing
      recorder.ondataavailable = (ev) => {
        if (ev.data.size) chunks.push(ev.data);
        if (!sheet.isConnected && recorder?.state === 'recording') { dropped = true; recorder.stop(); }
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        recorder = null;
        if (!dropped) upload(new File(chunks, 'my-recording.webm', { type: 'video/webm' }));
      };
      recorder.start(1000);
      const preview = h('video', { class: 'rec-preview', muted: true, playsInline: true, autoplay: true });
      preview.srcObject = stream;
      body.replaceChildren(h('div', { class: 'col gap-m' }, preview,
        h('div', { class: 'muted' }, 'Do the task once, saying each step out loud. Stop when you finish.'),
        h('button', { class: 'btn btn-accent', type: 'button', onclick: () => recorder?.stop() }, '■ Stop and get the steps')));
    } catch { say('The camera or microphone is blocked. Allow them in the address bar.'); }
  }

  videoIn.addEventListener('change', () => upload(videoIn.files[0]));
  manualIn.addEventListener('change', () => { upload(null, null, [...manualIn.files]); manualIn.value = ''; });

  menu();
  const sheet = showSheet('New guide', h('div', { class: 'col gap-m' },
    h('div', { class: 'sheet-head' }, h('h2', { class: 'sheet-title' }, 'New guide'),
      h('button', { class: 'sheet-close', type: 'button', 'aria-label': 'Close', onclick: closeAll }, icon('x'))),
    !live && h('div', { class: 'note info' }, document.documentElement.hasAttribute('data-static')
      ? 'This is the static demo: making your own guides needs the app running with a Meta API key.'
      : 'Add a Meta API key to make your own guides. Samples and replays work without one.'),
    body, busy, note, videoIn, manualIn, snapIn,
    h('p', { class: 'sheet-fine' }, 'Use something you have the rights to. It’s sent to Meta’s AI to find the steps, and on the hosted demo it’s '
      + 'deleted after 24 hours. By continuing you confirm you’re 18 or older and agree to ', h('a', { href: '#/terms', onclick: closeAll }, 'the terms'), '.')));
  // Drop a video, a PDF or page photos anywhere on the sheet
  sheet.addEventListener('dragover', (e) => { e.preventDefault(); if (live) sheet.classList.add('drag'); });
  sheet.addEventListener('dragleave', () => sheet.classList.remove('drag'));
  sheet.addEventListener('drop', (e) => {
    e.preventDefault();
    sheet.classList.remove('drag');
    const files = [...e.dataTransfer.files];
    if (files.length && files.every(isManual)) upload(null, null, files);
    else if (files[0]) upload(files[0]);
  });
  return sheet;
}
