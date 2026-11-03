// Stand-in for a video or camera frame, drawn as SVG in 1280×720 frame coordinates (the same coordinates as
// CheckResult boxes). Thermostat wiring: a top-down wall backplate on a wood block, no live power.
// The Joy-Con tutorial gets a small stand-in of its own (shell + numbered screw tray).
// Geometry lives in scene-geometry.json.
import { h, s } from './ui/dom.js';
import { api } from './api.js';
import { app } from './state.js';
import G from './scene-geometry.json' with { type: 'json' };

const T = G.thermostat, J = G.joycon;
const PLATE = T.plate;
const ZONE_Y = T.zone.y, ZONE_W = T.zone.w, ZONE_H = T.zone.h, SCREW_Y = T.screw_y;
// Terminal zones left to right. Rc is joined to Rh by a jumper (single-transformer system).
export const TERMINALS = T.terminals.map((name, i) => ({ name, x: T.zone.x0 + i * T.zone.pitch }));
const zone = (name) => TERMINALS.find((t) => t.name === name);
const cx = (name) => zone(name).x + ZONE_W / 2;
const WIRES = T.wires; // colour, correct terminal, start x in the wall hole
const COLORS = T.colors;
const COPPER = T.copper;
/** Box of a wire's stripped end when it sits in a terminal at x = endX (frame coordinates). */
export const wireEndBox = (endX) => ({ x: endX + T.wire_end.dx, y: T.wire_end.y, w: T.wire_end.w, h: T.wire_end.h });
export const zoneBox = (name) => ({ x: zone(name).x, y: ZONE_Y, w: ZONE_W, h: ZONE_H });
export const PLATE_BOX = PLATE;

/** Tutorials whose media the browser draws (no MP4 or frames on the server): GET /api/config `stand_in_media`. */
export const drawsStandIn = (tid) => (app.config.stand_in_media || []).includes(tid);

/** A step's end frame: the real JPEG, or the drawn stand-in for tutorials without media. */
export function stepFrame(tid, step, sceneOpts) {
  if (drawsStandIn(tid)) return thermostatScene({ tutorial: tid, ...sceneOpts });
  return h('img', { class: 'frame-img', src: api.frameUrl(tid, step), alt: '', decoding: 'async' });
}

let uid = 0;

function defs(id) {
  return s('defs', {},
    s('radialGradient', { id: `${id}-desk`, cx: '40%', cy: '20%', r: '110%' },
      s('stop', { offset: '0', 'stop-color': '#463B31' }), s('stop', { offset: '1', 'stop-color': '#221D19' })),
    s('radialGradient', { id: `${id}-studio`, cx: '50%', cy: '20%', r: '100%' },
      s('stop', { offset: '0', 'stop-color': '#F1EEE8' }), s('stop', { offset: '1', 'stop-color': '#D3CEC4' })),
    s('pattern', { id: `${id}-grain`, width: 11, height: 720, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(4)' },
      s('rect', { width: 2, height: 720, fill: 'rgba(255,255,255,.03)' })),
    s('pattern', { id: `${id}-wood`, width: 680, height: 23, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(-2)' },
      s('rect', { width: 680, height: 2, fill: 'rgba(60,30,10,.16)' })),
    s('filter', { id: `${id}-shadow`, x: '-20%', y: '-20%', width: '140%', height: '150%' },
      s('feDropShadow', { dx: 0, dy: 8, stdDeviation: 9, 'flood-color': '#000', 'flood-opacity': 0.35 })));
}

export function tag(x, y, text, cls) {
  const w = text.length * 11.3 + 14;
  return s('g', { class: `scene-tag ${cls}`, transform: `translate(${x} ${y})` },
    s('rect', { width: w, height: 27, rx: 4.5 }),
    s('text', { x: 7, y: 19 }, text));
}

function outline(box, label, cls = 'measured', tagPos = 'top') {
  const { x, y, w, h } = box;
  const pad = 6.5;
  const ty = tagPos === 'top' ? -pad - 31 : tagPos === 'inside' ? pad : h + pad + 4;
  return s('g', { class: `scene-outline ${cls}`, transform: `translate(${x} ${y})` },
    s('rect', { x: -pad, y: -pad, width: w + 2 * pad, height: h + 2 * pad, rx: 11 }),
    label && tag(tagPos === 'inside' ? pad : -pad, ty, label, cls));
}

function pulse({ x, y, w, h }) {
  return s('g', { class: 'scene-pulse', transform: `translate(${x} ${y})` },
    s('rect', { x: -6.5, y: -6.5, width: w + 13, height: h + 13, rx: 11 }));
}

function wire(color, sx, d, copper) {
  return s('g', {},
    s('path', { d, fill: 'none', stroke: 'rgba(0,0,0,.35)', 'stroke-width': 17, 'stroke-linecap': 'round' }),
    s('path', { d, fill: 'none', stroke: COLORS[color], 'stroke-width': 13, 'stroke-linecap': 'round' }),
    s('path', { d: copper, fill: 'none', stroke: COPPER, 'stroke-width': 6, 'stroke-linecap': 'round' }));
}

function seated(color, sx, endX) {
  const q = T.seated;
  return wire(color, sx, `M${sx} ${q.y0}C${sx} ${q.c1y} ${endX} ${q.c2y} ${endX} ${q.y1}`, `M${endX} ${q.y1}L${endX} ${q.tip_y}`);
}

function loose(color, sx, i) {
  const q = T.loose, ey = q.ey0 + i * q.dey;
  return wire(color, sx, `M${sx} ${q.y0}C${sx} ${q.c1y} ${q.c2x} ${ey + 40} ${q.x1} ${ey}`, `M${q.x1} ${ey}L${q.tip_x} ${ey - 6}`);
}

function backplate(id) {
  return s('g', {},
    s('rect', { x: T.block.x, y: T.block.y, width: T.block.w, height: T.block.h, rx: 18, fill: '#B7834F', filter: `url(#${id}-shadow)` }),
    s('rect', { x: T.block.x, y: T.block.y, width: T.block.w, height: T.block.h, rx: 18, fill: `url(#${id}-wood)` }),
    s('rect', { x: PLATE.x, y: PLATE.y, width: PLATE.w, height: PLATE.h, rx: 46, fill: '#EEECE6', filter: `url(#${id}-shadow)` }),
    s('rect', { x: T.hole.x, y: T.hole.y, width: T.hole.w, height: T.hole.h, rx: 30, fill: '#3B3631' }),
    TERMINALS.map((t) => s('g', {},
      s('rect', { x: t.x, y: ZONE_Y, width: ZONE_W, height: ZONE_H, rx: 8, fill: '#DAD6CC' }),
      s('text', { x: t.x + ZONE_W / 2, y: ZONE_Y + 28, 'text-anchor': 'middle', fill: '#2A2A2E', 'font-size': 22, 'font-weight': 600 }, t.name),
      s('circle', { cx: t.x + ZONE_W / 2, cy: SCREW_Y, r: 16, fill: '#B9BCC2', stroke: '#7D8188', 'stroke-width': 2 }),
      s('path', { d: `M${t.x + ZONE_W / 2 - 9} ${SCREW_Y}H${t.x + ZONE_W / 2 + 9}`, stroke: '#5D6168', 'stroke-width': 3 }))),
    s('rect', { x: cx('Rc'), y: T.jumper_y, width: cx('Rh') - cx('Rc'), height: 8, rx: 3, fill: '#9A9EA6' }));
}

function joycon(id, variant, outlines, lbl) {
  const inTray = { before: 0, wrong: 3, end: 4 }[variant] ?? 4;
  const holes = J.holes, q = J.slot, sh = J.shell, tray = J.tray;
  const slot = (i) => ({ x: q.x0 + (i % q.cols) * q.dx, y: q.y0 + Math.floor(i / q.cols) * q.dy, w: q.w, h: q.h });
  const screw = (x, y) => s('g', {}, s('circle', { cx: x, cy: y, r: 13, fill: '#C9CCD2', stroke: '#6D7178', 'stroke-width': 2 }),
    s('path', { d: `M${x - 7} ${y}H${x + 7}M${x} ${y - 7}V${y + 7}`, stroke: '#5D6168', 'stroke-width': 2.5 }));
  const left = holes.slice(inTray);
  const kids = [
    s('rect', { x: sh.x, y: sh.y, width: sh.w, height: sh.h, rx: 110, fill: '#2F8FD8', filter: `url(#${id}-shadow)` }),
    s('rect', { x: sh.x + 25, y: sh.y + 25, width: sh.w - 50, height: sh.h - 50, rx: 92, fill: '#1F2A33' }),
    s('circle', { cx: J.stick.cx, cy: J.stick.cy, r: J.stick.r, fill: '#2A2A2E' }), s('circle', { cx: J.stick.cx, cy: J.stick.cy, r: 36, fill: '#3C3C42' }),
    holes.map(([x, y]) => s('circle', { cx: x, cy: y, r: 9, fill: '#111' })),
    left.map(([x, y]) => screw(x, y)),
    s('rect', { x: tray.x, y: tray.y, width: tray.w, height: tray.h, rx: 16, fill: '#E9E6DF', filter: `url(#${id}-shadow)` }),
    [...Array(9).keys()].map((i) => s('g', {}, s('rect', { ...(({ x, y, w, h }) => ({ x, y, width: w, height: h }))(slot(i)), rx: 10, fill: '#D6D2C8' }),
      s('text', { x: slot(i).x + 10, y: slot(i).y + 24, fill: '#55524C', 'font-size': 18 }, String(i + 1)))),
    [...Array(inTray).keys()].map((i) => screw(slot(i).x + 50, slot(i).y + 62))];
  if (outlines && variant === 'wrong') {
    const [x, y] = left[0];
    const box = { x: x - 18, y: y - 18, w: 36, h: 36 };
    kids.push(outline(slot(3), lbl('goes here'), 'ghost', 'bottom'), pulse(box), outline(box, lbl('screw · still in shell'), 'wrong', 'bottom'));
  } else if (outlines) {
    kids.push(outline(tray, lbl(`parts tray · ${inTray} screws`), 'measured', 'top'));
  }
  return kids;
}

/** variant: before | end | wrong · surface: desk | studio · tutorial: picks the Joy-Con stand-in for `joycon-*` */
export function thermostatScene({ variant = 'end', surface = 'desk', outlines = false, tags = true, hand = false, tutorial = '' } = {}) {
  const id = `ts${++uid}`;
  const lbl = (t) => (tags ? t : null);
  const kids = [defs(id),
    s('rect', { width: 1280, height: 720, fill: `url(#${id}-${surface})` }),
    surface === 'desk' && s('rect', { width: 1280, height: 720, fill: `url(#${id}-grain)` })];
  const isJoycon = String(tutorial).startsWith('joycon');
  if (isJoycon) {
    kids.push(...joycon(id, variant, outlines, lbl));
  } else {
    kids.push(backplate(id), outlines && variant !== 'wrong' && outline(PLATE, lbl('thermostat backplate · 0.99'), 'measured', 'inside'));
    if (variant === 'before') {
      kids.push(...WIRES.map((w, i) => loose(w.color, w.sx, i)),
        outlines && outline({ x: 1020, y: 200, w: 80, h: 400 }, lbl('wire ×5 · 0.96'), 'measured', 'top'));
    } else {
      const wrong = variant === 'wrong';
      kids.push(...WIRES.map((w) => {
        if (!wrong) return seated(w.color, w.sx, cx(w.to));
        if (w.color in T.wrong_dx) return seated(w.color, w.sx, cx('Rh') + T.wrong_dx[w.color]);
        return seated(w.color, w.sx, cx(w.to));
      }));
      if (outlines && wrong) {
        const blue = wireEndBox(cx('Rh') + T.wrong_dx.blue);
        kids.push(outline(zoneBox('C'), lbl('goes here'), 'ghost', 'top'), pulse(blue),
          outline(blue, lbl('blue wire · in Rh'), 'wrong', 'bottom'));
      } else if (outlines) {
        kids.push(outline(wireEndBox(cx('C')), lbl('blue wire · in C'), 'measured', 'bottom'));
      }
    }
  }
  if (hand) {
    kids.push(s('g', { class: 'scene-hand' },
      s('path', { d: 'M707 430C707 180 1033 180 1033 430V720H707Z' }),
      tag(860, 205, 'hand · SAM 3.1', 'hand')));
  }
  return s('svg', { viewBox: '0 0 1280 720', preserveAspectRatio: 'xMidYMid slice', class: 'scene', role: 'img',
    'aria-label': isJoycon ? 'Stand-in picture: an opened Joy-Con and a numbered screw tray'
      : 'Stand-in picture: a thermostat backplate on a wood block, no power connected' }, kids);
}

/** Outlines from a CheckResult's `objects` (frame coordinates), drawn over the live view. */
export function outlineLayer(objects, badNames = new Set(), size = [1280, 720]) {
  return s('svg', { viewBox: `0 0 ${size[0]} ${size[1]}`, preserveAspectRatio: 'xMidYMid slice', class: 'scene scene-overlay', 'aria-hidden': 'true' },
    objects.filter((o) => o.found && o.outline_svg).map((o) => {
      const cls = badNames.has(o.name) ? 'wrong' : 'measured';
      const [x1, y1, , y2] = o.box;
      const label = o.name;
      return s('g', { class: `scene-outline ${cls}` },
        s('path', { d: o.outline_svg }),
        tag(x1, y1 > 40 ? y1 - 34 : y2 + 6, label, cls));
    }));
}
