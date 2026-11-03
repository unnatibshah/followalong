// Keyboard shortcuts (UX doc §4). Single letters work only on the follow-along screens and can be turned off.
import { h } from './ui/dom.js';
import { app } from './state.js';
import { closeAll, isOpen, showDialog } from './overlays.js';

const ROWS = [['H', 'Help me (explain it again)', 'Lesson'], ['B', 'Back a step', 'Lesson'], ['S', 'Skip this step', 'Lesson'],
  ['P', 'Pause or resume', 'Lesson'], ['C', 'Check my work now', 'Lesson'], ['A', 'Show the step again', 'Lesson'],
  ['Y / R', 'Yes / No, when I ask', 'Lesson'], ['Hold V', 'Push to talk (when realtime voice is unavailable)', 'Lesson'],
  ['D', 'How it decided', 'Lesson'], ['Esc', 'Close a menu or overlay', 'Any']];

let handler = null;
/** The follow screen registers `(key) => handled`; pass null to remove. */
export function setShortcuts(fn) { handler = fn; }

export function initKeyboard() {
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (isOpen()) closeAll(); else window.dispatchEvent(new Event('fa:escape'));
      return;
    }
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (!handler || !app.keysOn || isOpen() || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
    if (handler(e.key.toLowerCase())) e.preventDefault();
  });
}

export function openKeyboardPanel() {
  const sw = h('button', { class: 'switch-row', role: 'switch', 'aria-checked': String(app.keysOn) },
    h('span', {}, 'Single-letter shortcuts'), h('span', { class: 'switch', 'aria-hidden': 'true' }));
  sw.addEventListener('click', () => {
    app.keysOn = !app.keysOn;
    sw.setAttribute('aria-checked', String(app.keysOn));
    window.dispatchEvent(new Event('fa:keys-changed'));
  });
  showDialog('Keyboard', [
    h('div', { class: 'kbd-rows' }, ROWS.map(([key, does, where]) =>
      h('div', { class: 'kbd-row' }, h('kbd', {}, key), h('span', {}, does), h('span', { class: 'kbd-where' }, where)))),
    sw,
    h('p', { class: 'dialog-note' }, 'They only work while you follow along. Esc always closes this panel.')]);
}
