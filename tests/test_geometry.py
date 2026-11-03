"""UT-01-07, 10-17 and terminal zones: measured-check geometry on synthetic masks (unit test cases §A, §B)."""

import numpy as np
import pytest

from server import sam
from server.checks import geometry as g

SHAPE = (720, 1280)


def rect(x1, y1, x2, y2):
    return g.rect_mask([x1, y1, x2, y2], SHAPE)


def inst(mask, score=0.9):
    return {"box": g.box_of(mask), "score": score, "mask": mask}


def test_ut01_present():
    assert g.present([inst(rect(0, 0, 9, 9))], 0.5)["pass"] is True
    assert g.present([], 0.5)["pass"] is False


def test_ut02_count():
    two = [inst(rect(0, 0, 9, 9)), inst(rect(20, 0, 29, 9))]
    assert g.count(two, 2)["pass"] and not g.count(two, 3)["pass"]


def test_ut03_inside_occluded_container_uses_hull():
    zone = rect(100, 100, 199, 199)
    zone[100:200, 140:160] = False  # a wire-shaped hole: the wire end hides that part of the zone
    wire = rect(140, 108, 159, 207)  # 92 of its 100 rows over the zone
    r = g.inside(wire, zone, 0.9)
    assert r["pass"] and r["value"] == 0.92


def test_ut04_inside_partly_outside():
    r = g.inside(rect(180, 100, 219, 199), rect(100, 100, 199, 199), 0.9)
    assert not r["pass"] and r["value"] == 0.5


def test_ut05_adjacent_with_side():
    screw = rect(100, 100, 139, 139)
    right = rect(140, 110, 179, 129)  # touching the screw's right side; shorter side 20 px
    assert g.adjacent(right, screw, "right", 0.15)["pass"]
    assert not g.adjacent(right, screw, "left", 0.15)["pass"]
    gap = rect(146, 110, 185, 129)  # 6 px = 30% of the shorter side
    assert not g.adjacent(gap, screw, "right", 0.15)["pass"]


def test_ut06_left_of_and_above_with_margin():
    a, far, near = rect(100, 100, 109, 109), rect(228, 172, 237, 181), rect(125, 114, 134, 123)  # 10% / 2% apart
    assert g.left_of(a, far, 0.05)["pass"] and not g.left_of(a, near, 0.05)["pass"]
    assert g.above(a, far, 0.05)["pass"] and not g.above(a, near, 0.05)["pass"]


def test_ut07_area_ratio():
    b = rect(0, 0, 9, 9)  # 100 px
    assert g.area_ratio(rect(0, 0, 18, 9), b, 2.0, 0.25)["pass"]  # 1.9
    assert not g.area_ratio(rect(0, 0, 29, 9), b, 2.0, 0.25)["pass"]  # 3.0


def test_ut10_mask_placed_at_box_top_left():
    raster = np.ones((45, 100), dtype=np.uint8)
    m = sam.place(raster, (353, 382, 453, 427), (1280, 720))
    assert g.box_of(m) == [353, 382, 452, 426] and m.sum() == 4500


def test_ut11_downsampled_mask_is_scaled_to_its_box():
    raster = np.zeros((20, 50), dtype=np.uint8)
    raster[:, :25] = 1  # left half
    m = sam.place(raster, (353, 382, 453, 427), (1280, 720))
    assert g.box_of(m) == [353, 382, 402, 426] and m.sum() == 50 * 45


def test_ut13_tolerance_is_a_parameter():
    zone, wire = rect(100, 100, 199, 199), rect(140, 108, 159, 207)
    assert g.inside(wire, zone, 0.9)["pass"] and not g.inside(wire, zone, 0.95)["pass"]


@pytest.mark.parametrize("hand,blocked", [
    (rect(400, 200, 599, 347), True),             # ~5% of the 1024x576 work area (UT-14)
    (rect(0, 0, 127, 71), False),                  # only inside the 10% border (UT-15)
    (rect(400, 200, 400 + 58, 200 + 49), False),   # ~0.5% (UT-16)
    (None, False),                                 # no hand (UT-17)
])
def test_ut14_17_hand_gate(hand, blocked):
    assert (g.hand_cover([hand] if hand is not None else [], SHAPE, 0.10) > 0.01) is blocked


def test_ut16_exactly_one_percent_is_not_blocked():
    work = np.zeros((576, 1024), dtype=bool)
    work.flat[: int(work.size * 0.01)] = True
    m = np.zeros(SHAPE, dtype=bool)
    m[72:648, 128:1152] = work
    assert g.hand_cover([m], SHAPE, 0.10) == pytest.approx(0.01, abs=1e-5)
    assert not g.hand_cover([m], SHAPE, 0.10) > 0.01


ORDER = ["Rc", "Rh", "C", "W", "Y", "G"]
SCREWS = [inst(rect(447 + 72 * i, 214, 480 + 72 * i, 246)) for i in range(6)]


def test_terminal_zones_left_to_right_and_count():
    zones = g.terminal_zones(list(reversed(SCREWS)), ORDER, 0.9, 2.0)
    assert list(zones) == ORDER and zones["Rc"][0] < zones["Rh"][0] < zones["G"][0]
    assert g.terminal_zones(SCREWS[:5], ORDER, 0.9, 2.0) is None


def test_wire_end_in_terminal_zone():
    zones = g.terminal_zones(SCREWS, ORDER, 0.9, 2.0)
    blue_in_rh = rect(540, 286, 552, 450)  # insulation starts just under the Rh screw
    end = g.wire_end(blue_in_rh, zones)
    assert g.inside(end, g.rect_mask(zones["Rh"], SHAPE), 0.9)["pass"]
    assert g.inside(end, g.rect_mask(zones["C"], SHAPE), 0.9)["value"] == 0.0
    loose = rect(900, 400, 1000, 420)  # nowhere near the terminal row
    assert g.inside(g.wire_end(loose, zones), g.rect_mask(zones["C"], SHAPE), 0.9)["value"] == 0.0


def test_outline_svg_is_a_closed_path_in_frame_coordinates():
    path = g.outline_svg(rect(100, 200, 140, 260))
    assert path.startswith("M100 200") and path.endswith("Z")
    assert g.outline_svg(np.zeros(SHAPE, dtype=bool)) == ""


# ---- size filter (FR-44) ----

def disc(cx, cy, r):
    yy, xx = np.mgrid[:SHAPE[0], :SHAPE[1]]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r


def test_ut08_size_filter_by_aspect_ratio():
    ribbon = inst(rect(100, 100, 299, 149))   # 4:1
    stick = inst(disc(600, 300, 50))          # 1:1
    kept = g.size_filter([ribbon, stick], {"size": "large"}, SHAPE[0] * SHAPE[1], 0.2, 0.5)
    assert kept == [stick]


def test_ut08_a_diagonal_ribbon_is_still_long():
    """The minimum-area rectangle, not the axis-aligned box: a 45° ribbon has a square box but stays 4:1."""
    yy, xx = np.mgrid[:SHAPE[0], :SHAPE[1]]
    diag = inst((np.abs((xx - 400) - (yy - 300)) <= 15) & (np.abs((xx - 400) + (yy - 300)) <= 120))
    assert g.elongation(diag["mask"]) > 3
    assert g.size_filter([diag], {"size": "large"}, None, 0.2, 0.5) == []


def test_ut09_size_filter_tie_break_by_relative_area():
    stick, screw = inst(disc(300, 300, 25)), inst(disc(600, 300, 8))   # ~2,000 and ~200 px, both 1:1
    assert g.size_filter([screw, stick], {"size": "large"}, None, 0.2, 0.5) == [stick]
    assert g.size_filter([screw, stick], {"size": "small"}, None, 0.2, 0.5) == [screw]
    assert g.size_filter([screw, stick], {"size": None}, None, 0.2, 0.5) == [screw, stick]  # no size: untouched


def test_size_filter_area_band_share_of_the_frame_or_a_reference():
    """Joy-Con step 2 on real SAM 3.1 ("circle"): shell screws 0.00064 of the frame, empty screw holes 0.0004, the
    spare stick 0.011. The band 0.0005-0.002 keeps the 4 screws."""
    frame = SHAPE[0] * SHAPE[1]
    screws = [inst(disc(800 + 60 * i, 200, 13.5)) for i in range(4)]     # ~580 px = 0.00063
    holes = [inst(disc(400 + 60 * i, 500, 11)) for i in range(4)]        # ~380 px = 0.0004
    stick = inst(disc(1200, 640, 56))                                    # ~9,850 px = 0.0107
    band = {"min_area": 0.0005, "max_area": 0.002}
    assert g.size_filter(screws + holes + [stick], band, frame, 0.2, 0.5) == screws
    assert g.size_filter(screws + holes, {**band, "max_area": None}, frame, 0.2, 0.5) == screws
    assert g.size_filter(screws + holes, band, 0, 0.2, 0.5) == screws + holes  # no base (reference not found): no filter
    tray = 160_000  # as a share of a reference outline instead of the frame
    assert g.size_filter(screws + holes, {"min_area": 0.003, "max_area": 0.006}, tray, 0.2, 0.5) == screws
