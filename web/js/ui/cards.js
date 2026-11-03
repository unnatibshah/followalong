// Shared pieces of the app's pages: a guide card, a Continue card, a sideways slider and skeleton placeholders.
import { h, icon, mmss } from './dom.js';
import { app } from '../state.js';
import { drawsStandIn, stepFrame } from '../thermostatscene.js';
import { nav } from '../router.js';

export const isMine = (t) => t.source !== 'bundled';

function open(t) {
  app.tutorialId = t.id;
  app.mode = 'live';
  nav(t.status === 'ready' ? 'ready' : 'understanding');
}

/** A guide: its picture, status, length and where it came from. `onDelete` adds a remove button (your own guides). */
export function guideCard(t, { onDelete } = {}) {
  const ready = t.status === 'ready';
  const failed = t.status === 'failed';
  // The middle step's end frame: real videos often end on a logo card or a cutaway, not the finished work
  const preview = ready && t.step_count && !drawsStandIn(t.id) ? stepFrame(t.id, Math.ceil(t.step_count / 2))
    : h('div', { class: 'paper-preview' }, failed ? 'Couldn’t build this one' : 'Building your guide…');
  const tag = t.source === 'manual' ? 'From a manual' : isMine(t) ? 'Your video' : 'Sample';
  const status = ready ? 'Ready' : failed ? 'Failed' : 'Building…';
  const card = h('a', { href: `#/${ready ? 'ready' : 'understanding'}`, class: 'card', onclick: (e) => { if (e.target.closest('.card-del')) return; e.preventDefault(); open(t); } },
    h('div', { class: 'card-media' }, preview,
      h('span', { class: ['pill tl', !ready && 'muted-pill'] }, h('span', { class: ['dot', ready ? 'accent' : failed ? 'wrong' : 'pulse'], 'aria-hidden': 'true' }), status),
      (t.duration_s || t.manual) && h('span', { class: 'pill tr mono' }, t.manual && !t.duration_s ? `${t.manual.pages} p.` : mmss(t.duration_s)),
      onDelete && h('button', { class: 'card-del', type: 'button', 'aria-label': `Delete ${t.title}`, title: 'Delete',
        onclick: (e) => { e.preventDefault(); e.stopPropagation(); onDelete(t); } }, icon('trash'))),
    h('div', { class: 'card-title-row' }, h('span', { class: 'card-title' }, t.title)),
    h('div', { class: 'card-meta' }, h('span', { class: 'tag' }, tag), t.step_count ? `${t.step_count} steps` : '',
      t.creator ? h('span', { class: 'muted' }, `by ${t.creator}${t.source_url ? ' · CC BY' : ''}`) : ''));
  return card;
}

/** Where you left off: the step's picture, how far you got, one tap back in. */
export function continueCard(p) {
  const pct = Math.round(((p.step - 1) / Math.max(1, p.total)) * 100);
  const fill = h('span', { class: 'cont-fill' });
  fill.style.width = `${pct}%`; // CSSOM: the CSP forbids style attributes
  return h('a', { href: '#/consent', class: 'cont', onclick: (e) => { e.preventDefault(); app.tutorialId = p.id; app.mode = 'live'; nav('consent'); } },
    h('div', { class: 'cont-media' }, stepFrame(p.id, Math.max(1, p.step - 1))),
    h('div', { class: 'cont-body' },
      h('span', { class: 'cont-k' }, 'Continue'),
      h('span', { class: 'cont-t' }, p.title || 'Your guide'),
      h('span', { class: 'cont-s' }, `Step ${p.step} of ${p.total} · ${ago(p.at)}`),
      h('div', { class: 'cont-bar', role: 'progressbar', 'aria-valuenow': String(pct), 'aria-valuemin': '0', 'aria-valuemax': '100' },
        fill),
      h('span', { class: 'btn btn-accent btn-sm cont-go' }, 'Resume', icon('arrow'))));
}

/** A row that slides sideways (swipe, shift-wheel or the arrows); snaps to whole cards. */
export function slider(label) {
  const grid = h('div', { class: 'cards', role: 'group', 'aria-label': label });
  const prev = h('button', { class: 'cards-nav', type: 'button', 'aria-label': `Previous ${label.toLowerCase()}` }, '‹');
  const next = h('button', { class: 'cards-nav', type: 'button', 'aria-label': `More ${label.toLowerCase()}` }, '›');
  const bar = h('div', { class: 'cards-arrows' }, prev, next);
  const step = () => (grid.firstElementChild?.getBoundingClientRect().width || 300) + 24;
  const page = (dir) => grid.scrollBy({ left: dir * Math.max(step(), Math.floor(grid.clientWidth / step()) * step()) });
  prev.addEventListener('click', () => page(-1));
  next.addEventListener('click', () => page(1));
  const sync = () => {
    if (!grid.isConnected) { removeEventListener('resize', sync); return; } // left the page
    prev.disabled = grid.scrollLeft < 4;
    next.disabled = grid.scrollLeft + grid.clientWidth > grid.scrollWidth - 4;
    bar.hidden = grid.scrollWidth <= grid.clientWidth + 4;
  };
  grid.addEventListener('scroll', sync, { passive: true });
  addEventListener('resize', sync);
  return { grid, arrows: bar, fill: (cards) => { grid.replaceChildren(...cards); requestAnimationFrame(sync); } };
}

/** Grey placeholder cards while the list loads. */
export const skeletons = (n = 3) => Array.from({ length: n }, () => h('div', { class: 'card skel', 'aria-hidden': 'true' },
  h('div', { class: 'card-media skel-box' }), h('div', { class: 'skel-line' }), h('div', { class: 'skel-line short' })));

/** "just now", "5 min ago", "yesterday", "3 days ago", or the date. */
export function ago(at) {
  const s = (Date.now() - at) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 172800) return 'yesterday';
  if (s < 604800) return `${Math.round(s / 86400)} days ago`;
  return new Date(at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

/** A friendly empty state with one clear next step. */
export const empty = (title, text, action) => h('div', { class: 'empty' },
  h('div', { class: 'empty-title' }, title), h('div', { class: 'empty-text' }, text), action || '');
