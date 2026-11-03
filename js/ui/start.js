// Home: the welcome (first visit), Continue, your guides, and sample guides to explore. Making a guide lives in the
// ＋ New guide sheet (create.js); past sessions in Activity; everything you made in Library.
import { h, icon } from './dom.js';
import { api } from '../api.js';
import { app } from '../state.js';
import { stepFrame } from '../thermostatscene.js';
import { runs, unfinished } from '../progress.js';
import { openCreate } from './create.js';
import { continueCard, guideCard, isMine, skeletons, slider } from './cards.js';

const hello = () => { const hr = new Date().getHours(); return hr < 5 ? 'Good evening' : hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening'; };

export async function render(root) {
  const live = app.config.live_enabled;
  const staticDemo = document.documentElement.hasAttribute('data-static');
  const left = live ? unfinished().slice(0, 3) : [];
  const past = runs();
  // A first visit gets the full welcome; once you have something going, Home is about your work
  const returning = left.length > 0 || past.length > 0;

  // The first 5 seconds: what it is, what goes in, what you get, who it's for, what to press
  const toLib = () => root.querySelector('#fa-explore').scrollIntoView({ behavior: 'smooth' });
  const hero = h('section', { class: ['wrap hero', returning && 'compact'] },
    h('div', { class: 'hero-copy' },
      returning && h('span', { class: 'hero-hello' }, `${hello()}${past.length ? ` \u00b7 ${past.length} session${past.length === 1 ? '' : 's'} so far` : ''}`),
      h('span', { class: 'hero-eyebrow' }, h('span', { class: 'dot accent', 'aria-hidden': 'true' }), 'Real-time visual guidance for hands-on work'),
      h('h1', { class: 'display' }, 'Instructions that see your work.'),
      h('p', { class: 'lede' }, 'FollowAlong turns any video, manual or set of photos into a live, step-by-step guide. It follows along '
        + 'through your camera, checks every step, and tells you the moment something\u2019s off, so it\u2019s right the first time.'),
      h('div', { class: 'hero-inputs', role: 'list', 'aria-label': 'What you can start from' },
        [['▶', 'Video'], ['📄', 'Manual'], ['📷', 'Photos of the pages']].map(([i, t]) =>
          h('span', { class: 'hero-chip', role: 'listitem' }, h('span', { 'aria-hidden': 'true' }, i), t)),
        h('span', { class: 'hero-arrow', 'aria-hidden': 'true' }, '→'),
        h('span', { class: 'hero-chip strong', role: 'listitem' }, 'A guide that checks every step')),
      h('div', { class: 'hero-actions' },
        h('a', { href: '#/replay', class: 'btn btn-accent btn-xl glow' }, 'See it in action', icon('arrow')),
        returning ? h('button', { class: 'btn btn-white btn-xl', type: 'button', onclick: openCreate }, icon('plus'), 'New guide')
          : h('button', { class: 'btn-underline', onclick: toLib }, 'Start a guide ↓')),
      h('div', { class: 'hero-uses', 'aria-label': 'Built for' },
        h('span', { class: 'fine' }, 'Built for'),
        ['Electronics', 'Repairs & installs', 'Assembly', 'Training', 'Robotics'].map((t) => h('span', { class: 'hero-use' }, t)))),
    h('div', { class: 'hero-art' },
      h('a', { href: '#/replay', class: 'hero-frame', 'aria-label': 'See it in action' },
        // A real check: the CC BY tutorial's step-3 frame with only the LED recoloured (web/img/README.md)
        h('img', { class: 'frame-img', src: 'img/hero-mistake.jpg', alt: 'A breadboard with the wrong, orange LED pushed in', decoding: 'async' }),
        h('div', { class: 'hero-badge' }, icon('x'), h('span', {}, 'Not quite')),
        h('div', { class: 'hero-inset' }, stepFrame('breadboard-led', 2), h('span', { class: 'inset-tag' }, '3 s loop')),
        h('div', { class: 'hero-hint' }, "That's the orange LED. Use the round blue one, legs in the same rows.")),
      h('div', { class: 'hero-legend', 'aria-hidden': 'true' },
        ['shows the step', 'sees your work', 'flags the mistake', 'you fix it', 'next step'].flatMap((t, i) =>
          [i ? h('span', { class: 'rule' }) : null, h('span', { class: i === 2 ? 'slip' : '' }, t)]))));

  const how = h('section', { class: 'wrap' }, h('div', { class: 'how' },
    h('div', { class: 'how-lead' }, 'The instructions you already have, turned into a guide that knows when you\u2019ve got it right.'),
    [['01', 'Bring any instructions', 'A how-to video, a PDF manual, or photos of a paper one. Muse Spark turns it into exact steps: which part, where it goes, which way round, and what to be careful of.'],
      ['02', 'It sees your work', 'Your phone or laptop camera follows along. SAM 3.1 measures what\u2019s there and Muse Spark judges what geometry can\u2019t, so every step is checked before the next one starts.'],
      ['03', 'Hands-free and safe', 'Ask, interrupt, go back, by voice, in 7 languages. Risky steps wait for your confirmation, and teams see exactly where people get stuck.']].map(([n, t, d]) =>
      h('div', { class: 'how-item' }, h('span', { class: 'mono muted' }, n), h('span', { class: 'how-title' }, t), h('span', { class: 'how-text' }, d)))));

  // The purpose is always on Home: the full welcome on a first visit, a compact one once you have something going
  const top = returning ? hero : h('div', {}, hero, how);

  const cont = left.length ? h('section', { class: 'wrap home-sec' },
    h('h2', { class: 'sec-title' }, 'Continue'), h('div', { class: 'cont-row' }, left.map(continueCard))) : '';

  const mine = slider('Your guides');
  const mineSec = h('section', { class: 'wrap home-sec', hidden: true },
    h('div', { class: 'sec-head' }, h('h2', { class: 'sec-title' }, 'Your guides'),
      h('div', { class: 'sec-tools' }, h('a', { href: '#/library', class: 'sec-link' }, 'See all'), mine.arrows)), mine.grid);

  const explore = slider('Sample guides');
  explore.fill(skeletons(4));
  // CC BY attribution: title, author, source and licence for every bundled video
  const credits = h('details', { class: 'credits', hidden: true }, h('summary', {}, 'Video credits and licences'));
  const exploreSec = h('section', { class: 'wrap home-sec', id: 'fa-explore' },
    h('div', { class: 'sec-head' }, h('div', {}, h('h2', { class: 'sec-title' }, 'Explore'),
      h('p', { class: 'sec-sub' }, 'Sample guides, each already split into checked steps. Made from Creative Commons videos.')),
    h('div', { class: 'sec-tools' }, explore.arrows)), explore.grid, credits);

  // Make your own: the same sheet as the tab bar's ＋
  const make = h('section', { class: 'wrap home-sec' }, h('button', { class: 'make', type: 'button', onclick: openCreate },
    h('span', { class: 'make-icon', 'aria-hidden': 'true' }, icon('plus')),
    h('span', { class: 'make-text' }, h('span', { class: 'make-title' }, 'Make a guide from your own instructions'),
      h('span', { class: 'make-sub' }, 'Paste a link, upload a video or a PDF manual, snap the pages, or record yourself.')),
    icon('chevron', 'make-go')));

  const note = !live && h('div', { class: 'wrap' }, h('div', { class: 'note info' }, staticDemo
    ? 'Static demo: replays and checklists only. For live camera checks, run the app from GitHub with a Meta API key.'
    : 'Add a Meta API key to use live mode. Replays work without one.'));

  root.append(h('div', { class: 'page-light home' }, note || '', top, cont, mineSec, exploreSec, make,
    h('footer', { class: 'footer' }, h('div', { class: 'wrap footer-row' },
      h('span', {}, 'FollowAlong \u00b7 built with Muse Spark, SAM 3.1 and Muse Voice Transcribe'),
      h('div', { class: 'footer-links' }, h('a', { href: '#/creator' }, 'For teams'), h('a', { href: '#/evaluation' }, 'Evaluation'),
        h('a', { href: 'https://github.com/unnatibshah/followalong', target: '_blank', rel: 'noopener' }, 'GitHub'),
        h('a', { href: '#/privacy' }, 'Privacy'), h('a', { href: '#/terms' }, 'Terms'),
        !staticDemo && h('a', { href: '#/ops' }, 'Status'),
        !app.config.hosted && !staticDemo && h('a', { href: '#/annotate' }, 'Help check (annotate)'))))));

  try {
    const tutorials = await api.tutorials();
    const own = tutorials.filter(isMine);
    mine.fill([h('button', { class: 'card card-new', type: 'button', onclick: openCreate },
      h('span', { class: 'card-new-icon', 'aria-hidden': 'true' }, icon('plus')), h('span', {}, 'New guide')), ...own.map((t) => guideCard(t))]);
    mineSec.hidden = !own.length;
    explore.fill(tutorials.filter((t) => !isMine(t)).map((t) => guideCard(t)));
    const credited = tutorials.filter((t) => t.source_url);
    credits.append(h('ul', {}, credited.map((t) => h('li', {}, `${t.title}: \u201c${t.source_title || t.title}\u201d by ${t.creator}, `,
      h('a', { href: t.source_url, target: '_blank', rel: 'noopener' }, 'source'), `, ${t.licence}. Cut into steps by FollowAlong.`))));
    credits.hidden = !credited.length;
  } catch {
    explore.grid.replaceWith(h('div', { class: 'note warn row' }, h('span', {}, 'Something went wrong loading the guides. Try a replay instead.'),
      h('a', { href: '#/replay', class: 'btn btn-dark' }, 'See it in action')));
  }
  return null;
}
