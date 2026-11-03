// Boot: load the server config, then start the router. No product data lives in the browser code.
import { api } from './api.js';
import { app } from './state.js';
import { startRouter } from './router.js';
import { initKeyboard } from './keyboard.js';
import { initDevMenu } from './ui/devmenu.js';
import { showOverlay } from './overlays.js';

// Browser errors reach the server log (message and place only, never page content), so a broken phone is seen.
let reported = 0;
const report = (message, where) => { if (reported++ < 5) api.clientError(String(message).slice(0, 300), String(where || location.hash).slice(0, 200)); };
window.addEventListener('error', (e) => report(e.message, `${e.filename?.split('/').pop()}:${e.lineno} ${location.hash}`));
window.addEventListener('unhandledrejection', (e) => report(e.reason?.message || e.reason, `promise ${location.hash}`));

async function boot() {
  try {
    app.config = await api.config();
  } catch (err) {
    showOverlay('model', { title: "Can't reach FollowAlong", body: err.message, cta: 'Try again', onAction: () => location.reload() });
    return;
  }
  document.querySelector('.skip-link').addEventListener('click', (e) => { e.preventDefault(); document.getElementById('main').focus(); });
  initKeyboard();
  if (!app.config.hosted && !document.documentElement.hasAttribute('data-static')) initDevMenu();
  startRouter();
}

boot();
