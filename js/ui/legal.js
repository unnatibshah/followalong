// Privacy notice and terms of use (FR-31), routes #/privacy and #/terms. The text is web/legal/*.md, copied word for
// word from documentation/08-legal/01-terms-and-privacy-notice.md (a test keeps them equal). The small Markdown in
// it (headings, paragraphs, lists, one table, bold, italics, links) becomes DOM nodes; text only ever goes in as
// text, and placeholders such as <contact email> stay visible as placeholders.
import { h } from './dom.js';
import { api } from '../api.js';
import { nav } from '../router.js';
import { forgetAll } from '../progress.js';

// Delete my data: this browser's uploads, plan edits, session logs (server) and saved lesson progress (here)
function deleteMine() {
  const out = h('span', { class: 'small', role: 'status' });
  const btn = h('button', { class: 'btn btn-outline-light', onclick: async () => {
    if (!confirm('Delete your uploads, step edits, session logs and saved progress from this browser? This can\'t be undone.')) return;
    btn.disabled = true;
    try {
      const r = await api.deleteMe();
      forgetAll();
      out.textContent = `Deleted ${r.uploads} upload(s) and ${r.session_logs} session log(s).`;
    } catch (err) { out.textContent = err.message; btn.disabled = false; }
  } }, 'Delete my data');
  return h('div', { class: 'row gap-s delete-mine' }, btn, out);
}

const TITLES = { privacy: 'Privacy notice', terms: 'Terms of use' };

export const privacy = { render: (root) => page(root, 'privacy') };
export const terms = { render: (root) => page(root, 'terms') };

async function page(root, name) {
  // Back to wherever the link was (S5, S6, the S1 upload note, the footer); a direct visit goes to the start.
  const back = () => (history.length > 1 ? history.back() : nav(''));
  const other = name === 'privacy' ? 'terms' : 'privacy';
  const body = h('article', { class: 'legal-body' });
  root.append(h('div', { class: 'page-light' }, h('div', { class: 'wrap-narrow col gap-l legal' },
    h('button', { class: 'btn-text accent start', onclick: back }, '← Back'),
    body,
    h('a', { href: `#/${other}`, class: 'btn-text accent start' }, `Read the ${TITLES[other].toLowerCase()}`))));
  try {
    body.append(...markdown(await api.legal(name)));
    if (name === 'privacy' && !document.documentElement.hasAttribute('data-static')) body.append(deleteMine());
  } catch (err) {
    body.append(h('h1', { class: 'title-serif lg ink' }, TITLES[name]), h('p', { class: 'note warn' }, err.message));
  }
  document.title = `${TITLES[name]} · FollowAlong`;
  return null;
}

/** Block-level Markdown → nodes. The first `##` heading is the page's h1. */
export function markdown(text) {
  const out = [];
  let first = true;
  for (const block of text.split(/\n\s*\n/)) {
    const lines = block.split('\n').filter((l) => l.trim());
    if (!lines.length) continue;
    if (lines[0].startsWith('## ')) {
      out.push(first ? h('h1', { class: 'title-serif lg ink' }, inline(lines[0].slice(3))) : h('h2', {}, inline(lines[0].slice(3))));
      first = false;
    } else if (lines[0].startsWith('|')) {
      out.push(table(lines));
    } else {
      // Paragraph lines, then "- " list items (an indented line continues the item above).
      let para = [], list = null;
      for (const line of lines) {
        if (line.startsWith('- ')) { list ||= []; list.push(line.slice(2)); }
        else if (list && /^\s/.test(line)) list[list.length - 1] += ` ${line.trim()}`;
        else para.push(line.trim());
      }
      if (para.length) out.push(h('p', {}, inline(para.join(' '))));
      if (list) out.push(h('ul', {}, list.map((item) => h('li', {}, inline(item)))));
    }
  }
  return out;
}

function table(lines) {
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const [head, , ...rows] = lines;
  return h('div', { class: 'legal-table' }, h('table', {},
    h('thead', {}, h('tr', {}, cells(head).map((c) => h('th', { scope: 'col' }, inline(c))))),
    h('tbody', {}, rows.map((r) => h('tr', {}, cells(r).map((c) => h('td', {}, inline(c))))))));
}

const INLINE = /\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)\s]+)\)|`([^`]+)`|(<[^<>]+>)/g;

/** Inline Markdown → nodes: **bold**, *italic*, [link](url), `code`, and <placeholder>. */
export function inline(text) {
  const out = [];
  let at = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > at) out.push(text.slice(at, m.index));
    const [, bold, em, label, href, code, placeholder] = m;
    if (bold) out.push(h('strong', {}, inline(bold)));
    else if (em) out.push(h('em', {}, inline(em)));
    else if (label) out.push(/^https:\/\//.test(href) ? h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, label) : label);
    else if (code) out.push(h('code', {}, code));
    else out.push(h('span', { class: 'placeholder', title: 'Filled in before the hosted demo goes live' }, placeholder));
    at = m.index + m[0].length;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}
