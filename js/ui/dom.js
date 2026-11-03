// Tiny DOM helpers. Text always goes in as text nodes (never innerHTML), so model or user text is safe.

export function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  apply(el, props);
  append(el, kids);
  return el;
}

const SVGNS = 'http://www.w3.org/2000/svg';
export function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) el.setAttribute(k, v);
  append(el, kids);
  return el;
}

function apply(el, props) {
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
}

function append(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

export function clear(el) { el.replaceChildren(); return el; }

// Icons from the mockup (Lucide-style paths), drawn with currentColor.
const ICONS = {
  arrow: [['path', { d: 'M5 12h14' }], ['path', { d: 'm12 5 7 7-7 7' }]],
  play: [['polygon', { points: '7 4 20 12 7 20 7 4', class: 'fill' }]],
  pause: [['rect', { x: 6, y: 4, width: 4, height: 16, rx: 1, class: 'fill' }], ['rect', { x: 14, y: 4, width: 4, height: 16, rx: 1, class: 'fill' }]],
  x: [['path', { d: 'M18 6 6 18' }], ['path', { d: 'm6 6 12 12' }]],
  check: [['path', { d: 'M20 6 9 17l-5-5' }]],
  hand: ['M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2', 'M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2', 'M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8',
    'M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15'].map((d) => ['path', { d }]),
  ruler: [['path', { d: 'M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.41 2.41 0 0 1 0-3.4l2.6-2.6a2.41 2.41 0 0 1 3.4 0Z' }],
    ['path', { d: 'm14.5 12.5 2-2' }], ['path', { d: 'm11.5 9.5 2-2' }], ['path', { d: 'm8.5 6.5 2-2' }]],
  eye: [['path', { d: 'M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z' }], ['circle', { cx: 12, cy: 12, r: 3 }]],
  upload: [['path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }], ['polyline', { points: '17 8 12 3 7 8' }], ['line', { x1: 12, x2: 12, y1: 3, y2: 15 }]],
  spin: [['circle', { cx: 12, cy: 12, r: 9, class: 'track' }], ['path', { d: 'M21 12a9 9 0 0 0-9-9' }]],
  // App shell (tab bar, New guide sheet, library)
  home: [['path', { d: 'm3 10 9-7 9 7' }], ['path', { d: 'M5 9v11a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9' }]],
  library: [['path', { d: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20' }], ['path', { d: 'M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z' }]],
  plus: [['path', { d: 'M12 5v14' }], ['path', { d: 'M5 12h14' }]],
  clock: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7v5l3 2' }]],
  link: [['path', { d: 'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71' }], ['path', { d: 'M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71' }]],
  video: [['rect', { x: 2, y: 6, width: 14, height: 12, rx: 2 }], ['path', { d: 'm22 8-6 4 6 4V8Z' }]],
  file: [['path', { d: 'M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2Z' }], ['path', { d: 'M14 2v6h6' }], ['path', { d: 'M8 13h8' }], ['path', { d: 'M8 17h5' }]],
  camera: [['path', { d: 'M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z' }], ['circle', { cx: 12, cy: 13, r: 3 }]],
  record: [['circle', { cx: 12, cy: 12, r: 9 }], ['circle', { cx: 12, cy: 12, r: 4, class: 'fill' }]],
  search: [['circle', { cx: 11, cy: 11, r: 7 }], ['path', { d: 'm21 21-4.3-4.3' }]],
  trash: [['path', { d: 'M3 6h18' }], ['path', { d: 'M8 6V4h8v2' }], ['path', { d: 'M19 6l-1 14H6L5 6' }]],
  chevron: [['path', { d: 'm9 18 6-6-6-6' }]],
};

export function icon(name, cls = '') {
  return s('svg', { viewBox: '0 0 24 24', class: `icon icon-${name} ${cls}`, 'aria-hidden': 'true', focusable: 'false' },
    ICONS[name].map(([tag, attrs]) => s(tag, attrs)));
}

export const mmss = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
export const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
