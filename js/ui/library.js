// Library: every guide you made (videos, links, manuals, snapped pages) and the sample guides, searchable.
import { h, icon } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { showDialog, closeAll, showError, toast } from '../overlays.js';
import { openCreate } from './create.js';
import { clearProgress } from '../progress.js';
import { empty, guideCard, isMine, skeletons } from './cards.js';

let tab = 'mine'; // remembered while the app is open

export async function render(root) {
  const grid = h('div', { class: 'lib-grid' }, skeletons(6));
  const search = h('input', { type: 'search', class: 'lib-search', placeholder: 'Search guides', 'aria-label': 'Search guides' });
  const tabs = h('div', { class: 'seg-tabs', role: 'tablist', 'aria-label': 'Which guides' });
  let all = [];

  const remove = (t) => showDialog(`Delete “${t.title}”?`, [h('p', { class: 'dialog-body' }, 'The guide, its video and its steps are deleted. This can’t be undone.')],
    [h('button', { class: 'btn btn-ghost-dark', onclick: closeAll }, 'Cancel'),
      h('button', { class: 'btn btn-accent', onclick: async () => {
        closeAll();
        try { await api.deleteTutorial(t.id); clearProgress(t.id); all = all.filter((x) => x.id !== t.id); draw(); toast('Guide deleted.'); } catch (err) { showError(err); }
      } }, 'Delete')]);

  function draw() {
    const mine = all.filter(isMine);
    const list = tab === 'mine' ? mine : all.filter((t) => !isMine(t));
    tabs.replaceChildren(...[['mine', `Your guides · ${mine.length}`], ['samples', `Samples · ${all.length - mine.length}`]].map(([k, label]) =>
      h('button', { class: ['seg-tab', tab === k && 'on'], role: 'tab', type: 'button', 'aria-selected': String(tab === k),
        onclick: () => { tab = k; draw(); } }, label)));
    const q = search.value.trim().toLowerCase();
    const shown = q ? list.filter((t) => `${t.title} ${t.creator || ''}`.toLowerCase().includes(q)) : list;
    if (!shown.length) {
      grid.replaceChildren(q ? empty('Nothing matches', `No guide has “${search.value.trim()}” in its title.`)
        : tab === 'mine' ? empty('No guides of your own yet', 'Turn a video, a PDF manual or photos of a paper one into a guide that checks every step.',
          h('button', { class: 'btn btn-accent', type: 'button', onclick: openCreate }, icon('plus'), 'New guide'))
          : empty('No samples here', 'Samples load from the server.'));
      return;
    }
    grid.replaceChildren(...shown.map((t) => guideCard(t, { onDelete: isMine(t) && app.config.live_enabled ? remove : null })));
  }
  search.addEventListener('input', draw);

  root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap app-page' },
    h('div', { class: 'page-head' }, h('div', {}, h('h1', { class: 'page-title' }, 'Library'),
      h('p', { class: 'page-sub' }, 'Everything you’ve made, and guides to try.')),
    h('button', { class: 'btn btn-accent', type: 'button', onclick: openCreate }, icon('plus'), 'New guide')),
    h('div', { class: 'lib-bar' }, tabs, h('label', { class: 'lib-search-wrap' }, icon('search'), search)),
    grid,
    h('p', { class: 'fine' }, 'Your guides are kept for this browser', app.config.hosted ? ' and deleted after 24 hours on the hosted demo.' : '.'))));

  try {
    all = await api.tutorials();
    if (tab === 'mine' && !all.some(isMine)) tab = 'samples'; // nothing of your own yet: show what there is
    draw();
  } catch (err) {
    grid.replaceChildren(empty('Couldn’t load the guides', err.message || 'Try again in a moment.'));
  }
  return null;
}
