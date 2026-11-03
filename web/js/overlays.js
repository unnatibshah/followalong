// Shared overlays (UX doc §2: cap reached · model error · camera or microphone denied · no API key), and toasts.
import { h } from './ui/dom.js';

const KINDS = {
  cap: ['Live checks are paused', 'Live checks are paused (demo budget reached). Watch a replay instead.', 'Watch a replay'],
  model: ['Something went wrong', "Meta's AI models didn't answer. Your progress is kept. Try again in a moment, or watch a replay.", 'Watch a replay'],
  cam: ['Camera blocked', 'Your browser blocked the camera. Allow it in the address bar, or watch a replay.', 'Watch a replay'],
  mic: ['Microphone blocked', 'Voice is off. Buttons and shortcuts still work.', 'OK'],
  key: ['No API key', 'Add a Meta API key to use live mode. Replays work without one.', 'Watch a replay'],
};

let open = null;
export const isOpen = () => !!open;

export function closeAll() {
  if (!open) return;
  const { el, restore } = open;
  open = null;
  el.remove();
  if (restore && restore.isConnected) restore.focus();
}

/** Show a dialog. `kind` picks the UX copy; `body` overrides the text (always set with textContent). */
export function showOverlay(kind, { title, body, cta, onAction } = {}) {
  closeAll();
  const [t, b, c] = KINDS[kind] || KINDS.model;
  const act = onAction || (c === 'OK' ? closeAll : () => { closeAll(); location.hash = '#/replay'; });
  return showDialog(title || t, [h('p', { class: 'dialog-body' }, body || b)],
    [h('button', { class: 'btn btn-ghost-dark', onclick: closeAll }, 'Close (Esc)'),
      h('button', { class: 'btn btn-accent', onclick: act }, cta || c)]);
}

export function showDialog(title, content, actions = []) {
  closeAll();
  const restore = document.activeElement;
  const dialog = h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': title, onclick: (e) => e.stopPropagation() },
    h('h2', { class: 'dialog-title' }, title), content, actions.length && h('div', { class: 'dialog-actions' }, actions));
  const el = h('div', { class: 'scrim', onclick: closeAll }, dialog);
  document.getElementById('overlays').append(el);
  open = { el, restore };
  (dialog.querySelector('.btn-accent') || dialog.querySelector('button'))?.focus();
  return el;
}

/** A light bottom sheet (a centred card on desktop), e.g. New guide. Closes like a dialog: Esc, the scrim, navigation. */
export function showSheet(label, content) {
  closeAll();
  const restore = document.activeElement;
  const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': label, onclick: (e) => e.stopPropagation() },
    h('div', { class: 'sheet-grip', 'aria-hidden': 'true' }), content);
  const el = h('div', { class: 'scrim sheet-scrim', onclick: closeAll }, sheet);
  document.getElementById('overlays').append(el);
  open = { el, restore };
  sheet.querySelector('button, input, a')?.focus();
  return sheet;
}

/** Map an ApiError to its overlay (UX doc). Screens handle the inline cases themselves first. */
export function showError(err) {
  if (err.code === 'cap_reached') return showOverlay('cap', { body: err.message });
  if (err.code === 'no_api_key') return showOverlay('key', { body: err.message });
  return showOverlay('model', { body: err.message });
}

let toastTimer;
export function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}
