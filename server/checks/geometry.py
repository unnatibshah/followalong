"""Measured checks in code (FR-44, TD §4.2). Pure functions on frame-size bool masks and [x1, y1, x2, y2] boxes
(inclusive pixels). Each check returns {"pass", "value", "threshold"}; tolerances are passed in from config/checks.toml.

Terminal zones (the thermostat): SAM can't read "Rh" off a backplate, so one request finds every terminal screw,
`terminal_zones` sorts them left to right and maps them to the plan's terminal order. A terminal's zone is a
rectangle centred on its screw, `zone_width` x the screw pitch wide, from the screw's top to `zone_below` x the
screw's height under it (a wire's bare end sits on or just under the screw; SAM's "blue wire" mask is the
insulation, which starts a few pixels below the screw). A wire end is "in" a terminal when at least `inside` of the
wire's pixels within the terminal row (the band all zones share) fall in that terminal's zone: `wire_end` cuts the
wire to that band, then `inside` measures it against the zone.
"""

import numpy as np


def box_of(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None


def union(masks: list[np.ndarray], shape: tuple[int, int]) -> np.ndarray:
    out = np.zeros(shape, dtype=bool)
    for m in masks:
        out |= m
    return out


def rect_mask(box: list[float], shape: tuple[int, int]) -> np.ndarray:
    m = np.zeros(shape, dtype=bool)
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    m[max(y1, 0):max(y2 + 1, 0), max(x1, 0):max(x2 + 1, 0)] = True
    return m


def filled_hull(mask: np.ndarray) -> np.ndarray:
    """The mask's convex hull, filled: a wire end over a terminal hides part of it, so its raw mask has a hole."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 3:
        return mask.copy()
    rows = np.unique(ys)
    left = np.array([xs[ys == y].min() for y in rows])
    right = np.array([xs[ys == y].max() for y in rows])
    pts = sorted(set(zip(left.tolist(), rows.tolist())) | set(zip(right.tolist(), rows.tolist())))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:  # monotone chain
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return mask.copy()
    x1, y1, x2, y2 = box_of(mask)
    gy, gx = np.mgrid[y1:y2 + 1, x1:x2 + 1]
    inside = np.ones(gx.shape, dtype=bool)
    for (ax, ay), (bx, by) in zip(hull, hull[1:] + hull[:1]):
        inside &= (bx - ax) * (gy - ay) - (by - ay) * (gx - ax) >= 0
    out = mask.copy()
    out[y1:y2 + 1, x1:x2 + 1] |= inside
    return out


def _centre(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


# ---- measured check types ----

def present(instances: list[dict], min_conf: float) -> dict:
    best = max((i["score"] or 0 for i in instances), default=0.0)
    return {"pass": bool(instances), "value": round(best, 2), "threshold": min_conf}


def count(instances: list[dict], n: int) -> dict:
    return {"pass": len(instances) == n, "value": len(instances), "threshold": n}


def inside(a: np.ndarray, b: np.ndarray, tol: float) -> dict:
    area = int(a.sum())
    value = float((a & filled_hull(b)).sum() / area) if area else 0.0
    return {"pass": value >= tol, "value": round(value, 2), "threshold": tol}


def adjacent(a: np.ndarray, b: np.ndarray, side: str, tol: float) -> dict:
    """Gap between the masks' boxes over A's shorter side, and A on `side` (left/right/above/below) of B.
    ponytail: box gap, not the exact mask-to-mask distance; fine for the compact parts this is used on."""
    ab, bb = box_of(a), box_of(b)
    if not ab or not bb:
        return {"pass": False, "value": None, "threshold": tol}
    dx = max(0, bb[0] - ab[2] - 1, ab[0] - bb[2] - 1)
    dy = max(0, bb[1] - ab[3] - 1, ab[1] - bb[3] - 1)
    gap = float(np.hypot(dx, dy)) / max(1, min(ab[2] - ab[0] + 1, ab[3] - ab[1] + 1))
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    on_side = {"left": ax < bx, "right": ax > bx, "above": ay < by, "below": ay > by}.get(side or "", True)
    return {"pass": gap <= tol and on_side, "value": round(gap, 2), "threshold": tol}


def left_of(a: np.ndarray, b: np.ndarray, margin: float) -> dict:
    d = (_centre(b)[0] - _centre(a)[0]) / a.shape[1]
    return {"pass": d >= margin, "value": round(d, 3), "threshold": margin}


def above(a: np.ndarray, b: np.ndarray, margin: float) -> dict:
    d = (_centre(b)[1] - _centre(a)[1]) / a.shape[0]
    return {"pass": d >= margin, "value": round(d, 3), "threshold": margin}


def area_ratio(a: np.ndarray, b: np.ndarray, ref: float, tol: float) -> dict:
    ratio = float(a.sum() / b.sum()) if b.sum() else 0.0
    return {"pass": abs(ratio / ref - 1) <= tol if ref else False, "value": round(ratio, 2), "threshold": tol}


# ---- size filter (FR-44, TD §4.2) ----

def elongation(mask: np.ndarray) -> float:
    """Long side / short side of the mask's minimum-area rectangle (searched in 3° steps; 1.0 = square or round).
    ponytail: angle search, not rotating calipers; within ~1% of the exact rectangle at this step."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 2:
        return 1.0
    pts = np.stack([xs, ys], 1)[:: max(1, len(xs) // 4000)].astype(float)
    best = (np.inf, 1.0)
    for a in np.radians(np.arange(0, 90, 3)):
        u, v = pts @ [np.cos(a), np.sin(a)], pts @ [-np.sin(a), np.cos(a)]
        w, h = np.ptp(u) + 1, np.ptp(v) + 1
        if w * h < best[0]:
            best = (w * h, max(w, h) / min(w, h))
    return float(best[1])


def size_filter(instances: list[dict], obj: dict, base_area: float | None, aspect_tol: float, tie_ratio: float) -> list[dict]:
    """Keep the instances that match the object's size (TD §4.2), before any measured check.
    `min_area` / `max_area`: the outline's area as a share of `base_area` (the frame, or the object's `area_ref`);
    skipped when there's no base (the reference wasn't found). `size` `large` / `small`: a round part (minimum-area
    rectangle within `aspect_tol` of 1:1, so a 4:1 ribbon cable goes), then relative area in the same frame as the
    tie-break: `large` keeps instances at least `tie_ratio` x the largest, `small` at most 1 / `tie_ratio` x the
    smallest. Both work at any camera distance; frame shares assume the camera height the tutorial asks for."""
    keep = instances
    lo, hi = obj.get("min_area"), obj.get("max_area")
    if (lo is not None or hi is not None) and base_area:
        keep = [i for i in keep if (lo or 0) <= i["mask"].sum() / base_area <= (hi if hi is not None else np.inf)]
    if obj.get("size") in ("large", "small") and keep:
        keep = [i for i in keep if elongation(i["mask"]) <= 1 + aspect_tol]
        areas = [int(i["mask"].sum()) for i in keep]
        if not keep:
            pass
        elif obj["size"] == "large":
            keep = [i for i, a in zip(keep, areas) if a >= tie_ratio * max(areas)]
        else:
            keep = [i for i, a in zip(keep, areas) if a <= min(areas) / tie_ratio]
    return keep


# ---- hand gate and terminals ----

def hand_cover(hands: list[np.ndarray], shape: tuple[int, int], border: float) -> float:
    """Share of the work area (the frame minus `border` on each side) covered by hand masks."""
    h, w = shape
    by, bx = int(h * border), int(w * border)
    area = union(hands, shape)[by:h - by, bx:w - bx]
    return float(area.mean()) if area.size else 0.0


def terminal_zones(instances: list[dict], order: list[str], zone_width: float, zone_below: float) -> dict | None:
    """{label: zone box}, or None when the number of terminals found isn't len(order)."""
    if len(instances) != len(order) or len(order) < 2:
        return None
    boxes = sorted((i["box"] for i in instances), key=lambda b: (b[0] + b[2]) / 2)
    centres = [(b[0] + b[2]) / 2 for b in boxes]
    pitch = float(np.median(np.diff(centres)))
    # Every zone spans the same rows (the terminal row), so a wire end is measured against one band.
    top, bottom = min(b[1] for b in boxes), max(b[3] for b in boxes)
    height = float(np.median([b[3] - b[1] + 1 for b in boxes]))
    half = zone_width * pitch / 2
    return {label: [cx - half, top, cx + half, bottom + zone_below * height] for label, cx in zip(order, centres)}


def wire_end(wire: np.ndarray, zones: dict) -> np.ndarray:
    """The part of a wire mask inside the terminal row (the y band every zone shares)."""
    y1 = int(min(z[1] for z in zones.values()))
    y2 = int(max(z[3] for z in zones.values()))
    out = np.zeros_like(wire)
    out[y1:y2 + 1] = wire[y1:y2 + 1]
    return out


def outline_svg(mask: np.ndarray, step: int = 3) -> str:
    """An SVG path around a mask for the live-view outline (FR-51): down its left edge, back up its right edge,
    one point every `step` rows. ponytail: fills concave gaps across a row (a U-bend reads as solid); trace real
    contours if the outlines need to hug bends."""
    rows = np.flatnonzero(mask.any(axis=1))
    if not len(rows):
        return ""
    rows = np.unique(np.append(rows[::step], rows[-1]))
    left = [int(np.argmax(mask[y])) for y in rows]
    right = [int(mask.shape[1] - 1 - np.argmax(mask[y, ::-1])) for y in rows]
    pts = [f"{x} {y}" for x, y in zip(left, rows)] + [f"{x + 1} {y}" for x, y in zip(right[::-1], rows[::-1])]
    return "M" + "L".join(pts) + "Z"


def rect_svg(box: list[float]) -> str:
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    return f"M{x1} {y1}H{x2}V{y2}H{x1}Z"
