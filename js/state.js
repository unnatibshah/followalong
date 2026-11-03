// What the screens share between routes. Holds only what the API returned plus the learner's choices.
// The chosen tutorial survives a reload (sessionStorage, this tab only), so S3/S4 never fall back to another one.
const TID = 'fa.tutorialId';
const stored = () => { try { return sessionStorage.getItem(TID); } catch { return null; } };

export const app = {
  config: null,        // GET /api/config
  _tid: stored(),
  get tutorialId() { return this._tid; },  // chosen on S1 (or the upload id after S2)
  set tutorialId(id) {
    this._tid = id;
    try { if (id) sessionStorage.setItem(TID, id); else sessionStorage.removeItem(TID); } catch { /* storage blocked */ }
  },
  mode: 'live',        // 'live' | 'replay'
  recordingId: null,   // chosen on S11
  sessionId: null,     // POST /api/sessions
  checkNo: {},         // per-step /checks counter for the current session
  reviewStep: 1,       // S4 selection (S12 "Edit checks" jumps here)
  keysOn: true,        // single-letter shortcuts (WCAG 2.1.4)
  voiceOn: null,       // null: not chosen yet → S6 turns it on (asks for the microphone); false: turned off
  resumeStep: null,    // set by "Continue from step N"; the lesson starts there once
  get lang() { try { return localStorage.getItem('fa.lang') || 'en'; } catch { return 'en'; } },
  set lang(code) { try { localStorage.setItem('fa.lang', code); } catch { /* storage blocked: English */ } },
};

export function newSession(id, mode) {
  app.sessionId = id;
  app.mode = mode;
  app.checkNo = {};
}

export const nextCheckNo = (step) => (app.checkNo[step] = (app.checkNo[step] || 0) + 1);
