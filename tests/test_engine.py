"""UT-20-38 (FR-46 verdict table, re-check, fallbacks, hints), always-ask and the terminal-count rule, with SAM and
Muse Spark stubbed."""

import asyncio
import time
import json
import os
from pathlib import Path

import pytest

from server.checks import engine
from server.checks import geometry as g
from server.gateway import GatewayError

SHAPE = (720, 1280)
PLAN = json.loads((Path(__file__).parent / "fixtures/stand-in-thermostat-plan.json").read_text(encoding="utf-8"))
STEP = {s["id"]: s for s in PLAN["steps"]}

M_PASS, M_FAIL = {"pass": True}, {"pass": False, "type": "inside"}
M_FAIL_VIEW = {"pass": False, "type": "left_of"}  # depends on the tutorial's camera view


def J(ok, conf):
    return {"pass": ok, "confidence": conf}


@pytest.mark.parametrize("measured,judged,found,expected", [
    ([M_PASS], [], True, "done"),                                  # UT-20
    ([], [J(True, 0.9)], True, "done"),                            # UT-21
    ([M_PASS], [J(True, 0.9)], True, "done"),                      # UT-22
    ([M_FAIL], [], True, "wrong"),                                 # UT-23
    ([M_FAIL], [J(False, 0.9)], True, "wrong"),                    # UT-24
    ([M_FAIL], [J(True, 0.9)], True, "wrong"),                     # UT-25 a measured fail wins (CR-004)
    ([M_PASS], [J(False, 0.9)], True, "wrong"),                    # UT-26
    ([], [J(False, 0.9)], True, "wrong"),                          # UT-27
    ([M_PASS], [J(True, 0.5)], True, "unsure"),                    # UT-28 a
    ([M_PASS], [J(True, 0.9), J(True, 0.5)], True, "unsure"),      # UT-28 b
    ([M_FAIL], [J(True, 0.9), J(True, 0.5)], True, "wrong"),       # UT-28 c measured fail wins (CR-004)
    ([M_PASS], [J(False, 0.5)], True, "unsure"),                   # UT-28 d
    ([M_PASS], [J(True, 0.9)], False, "unsure"),                   # UT-29
    ([M_PASS], [J(False, 0.7)], True, "wrong"),                    # UT-30 at the threshold is confident
    ([M_PASS], [J(True, 0.7)], True, "done"),                      # UT-30
    ([M_FAIL_VIEW], [J(True, 0.9)], True, "unsure"),               # another setup / view: ask, never a false "wrong"
    ([M_FAIL_VIEW, M_FAIL], [J(True, 0.9)], True, "wrong"),        # a transferable fail still vetoes
])
def test_ut20_30_verdict_table(measured, judged, found, expected):
    assert engine.verdict(found, measured, judged, threshold=0.7) == expected


def test_always_ask_is_unsure_whatever_else_passes():
    assert engine.verdict(True, [M_PASS], [], ask=True) == "unsure"


# ---- engine with a stub SAM ----

def inst(box, score=0.9):
    return {"box": box, "score": score, "mask": g.rect_mask(box, SHAPE)}


SCREWS = [inst([447 + 72 * i, 214, 480 + 72 * i, 246]) for i in range(6)]
WIRES = {"blue wire": [inst([540, 286, 552, 450])],  # in Rh
         "white wire": [inst([675, 286, 687, 450])],  # in W
         "red wire": [inst([530, 286, 542, 450])]}


def stub(found: dict, calls: list, fail=()):
    async def segment(frame, concept, session_id=None):
        calls.append(concept)
        if concept in fail:
            raise GatewayError("failed", 503)
        return {"concept": concept, "instances": found.get(concept, []), "size": (1280, 720),
                "latency_ms": 1, "cost_usd": 0.0025}
    return segment


def run(monkeypatch, step, found, kind="full", fail=(), judged=None, judge_s=0.0):
    """`judged`: {check_id: (pass, confidence)} for the stub Muse Spark, or an exception to raise. Spark calls are
    appended to `calls` as ("spark", [check ids], crop box)."""
    calls = []
    monkeypatch.setattr(engine.sam, "segment", stub(found, calls, fail))

    async def judge(step, checks, frame, end_frame, box, session_id=None, end_box=None):
        calls.append(("spark", [c["id"] for c in checks], box))
        await asyncio.sleep(judge_s)
        if isinstance(judged, Exception):
            raise judged
        return {"results": [{"check_id": c["id"], "type": "judged", "kind": "judged", "pass": judged[c["id"]][0],
                             "confidence": judged[c["id"]][1], "reason": "r"} for c in checks],
                "hint": "Spark hint.", "latency_ms": 5, "cost_usd": 0.002, "reasoning_tokens": 10}
    monkeypatch.setattr(engine.spark, "judge", judge)
    return asyncio.run(engine.run(step, b"\xff\xd8\xff", kind, end_frame=b"end")), calls


@pytest.mark.parametrize("judged", [(True, 0.6), (True, 0.9), (False, 0.9)])
def test_blue_in_rh_is_wrong_with_the_measured_hint_whatever_spark_says(monkeypatch, judged):
    """CR-004 (UT-25): a measured fail is `wrong` with the measured hint; the check doesn't wait for Muse Spark."""
    start = time.monotonic()
    res, calls = run(monkeypatch, STEP[7], {"circle": SCREWS, **WIRES}, judged={"s7c2": judged}, judge_s=2.0)
    assert time.monotonic() - start < 1.0, "waited for Muse Spark"
    assert calls[0] == "hand" and not any(isinstance(c, tuple) for c in calls)  # a measured fail: no Muse Spark call
    assert sorted(c for c in calls[1:] if isinstance(c, str)) == ["blue wire", "circle"]  # terminals share one request
    assert res["verdict"] == "wrong" and res["results"] == [
        {"check_id": "s7c1", "type": "inside", "kind": "measured", "pass": False, "value": 0.0, "threshold": 0.9}]
    assert res["hint"] == "Blue wire goes in C, not Rh. Rh is 24 V power — this would blow your furnace fuse."
    assert {o["name"] for o in res["objects"] if o["found"]} == {"blue wire", "C terminal", "Rh terminal"}
    assert res["models"] == ["sam-3.1"] and res["cost_usd"] == 0.0075 and "judge" not in res["stages_ms"]


def test_spark_gets_the_crop_around_the_objects(monkeypatch):
    """A step with objects: Muse Spark runs after the object requests, on the union of the found objects' boxes."""
    start = time.monotonic()
    res, calls = run(monkeypatch, STEP[3], {"circle": SCREWS, **WIRES}, judged={"s3c2": (False, 0.9)}, judge_s=0.3)
    found = [o["box"] for o in res["objects"] if o["found"]]
    crop = [min(b[0] for b in found), min(b[1] for b in found), max(b[2] for b in found), max(b[3] for b in found)]
    assert calls[-1] == ("spark", ["s3c2"], crop) and isinstance(calls[1], str)  # after the object requests
    assert res["verdict"] == "wrong" and res["hint"] == "Spark hint."  # UT-37: judged fail, measured passed
    assert res["models"] == ["sam-3.1", "muse-spark-1.3"] and res["cost_usd"] == 0.0095  # UT-31
    assert set(res["stages_ms"]) == {"actor", "objects", "geometry", "judge"}
    assert [r["kind"] for r in res["results"]] == ["measured", "judged"]  # UT-31
    assert time.monotonic() - start < 1.0


def test_ut37_measured_hint_without_a_judged_failure(monkeypatch):
    step = {**STEP[7], "checks": STEP[7]["checks"][:1]}
    res, calls = run(monkeypatch, step, {"circle": SCREWS, **WIRES})
    assert res["verdict"] == "wrong" and res["hint"] == "Blue wire goes in C, not Rh. Rh is 24 V power — this would blow your furnace fuse."
    assert res["models"] == ["sam-3.1"] and not any(c[0] == "spark" for c in calls if isinstance(c, tuple))


def test_measured_and_judged_pass_is_done(monkeypatch):
    res, _ = run(monkeypatch, STEP[3], {"circle": SCREWS, **WIRES}, judged={"s3c2": (True, 0.95)})
    assert res["verdict"] == "done" and res["hint"] is None and res["fallback"] is None


def test_judged_only_step_without_objects_sends_the_whole_frame(monkeypatch):
    res, calls = run(monkeypatch, STEP[8], {}, judged={"s8c1": (False, 0.8)})
    assert calls == ["hand", ("spark", ["s8c1"], None)]
    assert res["verdict"] == "wrong" and res["hint"] == "Spark hint."  # UT-27


@pytest.mark.parametrize("err", [GatewayError("failed", 503), GatewayError("invalid_output")])
def test_ut36_spark_fails(monkeypatch, err):
    res, _ = run(monkeypatch, STEP[3], {"circle": SCREWS, **WIRES}, judged=err)
    assert res["verdict"] == "unsure" and res["fallback"] == "judge_failed" and res["models"] == ["sam-3.1"]
    assert [r["kind"] for r in res["results"]] == ["measured"]
    res, _ = run(monkeypatch, STEP[7], {"circle": SCREWS, **WIRES}, judged=err)
    assert res["verdict"] == "wrong" and res["fallback"] is None  # the measured failure stands; no ask


def test_always_ask_breaker_makes_no_model_call(monkeypatch):
    res, calls = run(monkeypatch, STEP[1], {}, judged={})
    assert calls == [] and res["verdict"] == "unsure" and res["cost_usd"] == 0.0


def test_ut34_spark_ignored_when_an_object_is_missing(monkeypatch):
    res, calls = run(monkeypatch, STEP[3], {"circle": SCREWS}, judged={"s3c2": (True, 0.9)}, judge_s=2.0)
    assert res["verdict"] == "unsure" and res["models"] == ["sam-3.1"] and res["results"] == []


def test_measured_only_step_is_done(monkeypatch):
    res, calls = run(monkeypatch, STEP[4], {"circle": SCREWS, **WIRES})
    assert res["verdict"] == "done" and res["hint"] is None
    assert not any(isinstance(c, tuple) for c in calls)  # UT-34: no Muse Spark call


def test_wrong_terminal_count_is_unsure_with_a_reason(monkeypatch):
    res, _ = run(monkeypatch, STEP[4], {"circle": SCREWS[:5], **WIRES})
    assert res["verdict"] == "unsure" and res["results"] == [] and "5 terminals, not 6" in res["hint"]


def test_soft_gate_hands_in_view_still_get_judged(monkeypatch):
    """The actor is outlined next to the objects, not before them: a hand in view doesn't stop the check."""
    res, calls = run(monkeypatch, STEP[7], {"hand": [inst([400, 200, 700, 500])], "circle": SCREWS, **WIRES})
    assert "hand" in calls and "blue wire" in calls and res["hand_blocked"] is False
    assert res["verdict"] == "wrong" and res["hint"].startswith("Blue wire goes in C")  # blue still in Rh


def test_soft_gate_waits_only_when_the_actor_hides_a_needed_part(monkeypatch):
    res, _ = run(monkeypatch, STEP[7], {"hand": [inst([300, 150, 900, 600])], "circle": SCREWS})  # no blue wire
    assert res["verdict"] == "waiting" and res["hand_blocked"] is True and "blue wire" in res["hint"]
    res, _ = run(monkeypatch, STEP[7], {"circle": SCREWS})  # same part missing, nothing covering: ask
    assert res["verdict"] == "unsure" and res["hand_blocked"] is False


def test_any_actor_phrase_and_none(monkeypatch):
    """A robot tutorial: SAM is asked for the actor the video names; "none" asks for nobody."""
    res, calls = run(monkeypatch, {**STEP[7], "actor": "robot gripper"}, {"circle": SCREWS, **WIRES})
    assert "robot gripper" in calls and "hand" not in calls
    res, calls = run(monkeypatch, {**STEP[7], "actor": "none"}, {"circle": SCREWS, **WIRES})
    assert "hand" not in calls and res["verdict"] == "wrong"


def test_hands_ok_step_never_waits_for_the_actor(monkeypatch):
    """A step where letting go would undo the work: a covering hand never turns a missing part into `waiting`."""
    res, _ = run(monkeypatch, {**STEP[7], "hands_ok": True}, {"hand": [inst([300, 150, 900, 600])], "circle": SCREWS})
    assert res["verdict"] == "unsure" and res["hand_blocked"] is False


def test_ut32_recheck_is_measured_only(monkeypatch):
    res, calls = run(monkeypatch, STEP[3], {"circle": SCREWS, **WIRES}, kind="recheck", judged={"s3c2": (False, 0.9)})
    assert res["verdict"] == "done" and [r["check_id"] for r in res["results"]] == ["s3c1"]
    assert not any(isinstance(c, tuple) for c in calls)


def test_ut33_recheck_of_judged_only_step_checks_presence(monkeypatch):
    step = {**STEP[1], "objects": [{"name": "red wire", "sam_prompt": "red wire", "size": None}]}
    res, _ = run(monkeypatch, step, WIRES, kind="recheck")
    assert res["verdict"] == "done" and res["results"][0]["type"] == "present"


def test_ut29_object_missing_is_unsure(monkeypatch):
    res, _ = run(monkeypatch, STEP[7], {"circle": SCREWS})
    assert res["verdict"] == "unsure" and not next(o for o in res["objects"] if o["name"] == "blue wire")["found"]


def test_ut35_sam_fails_after_retry(monkeypatch):
    res, _ = run(monkeypatch, STEP[7], {"circle": SCREWS, **WIRES}, fail=("blue wire",))
    assert res["verdict"] == "waiting" and res["fallback"] == "sam_failed"


def test_a_failed_actor_request_never_fails_the_check(monkeypatch):
    res, _ = run(monkeypatch, STEP[7], {"circle": SCREWS, **WIRES}, fail=("hand",))
    assert res["verdict"] == "wrong" and res["fallback"] is None


def test_ut38_object_requests_run_in_parallel(monkeypatch):
    started, gate = [], asyncio.Event()

    async def segment(frame, concept, session_id=None):
        if concept != "hand":
            started.append(concept)
            if len(started) == 3:
                gate.set()
            await asyncio.wait_for(gate.wait(), 1)  # deadlocks (times out) if requests ran one by one
        return {"concept": concept, "instances": [], "size": (1280, 720), "latency_ms": 1, "cost_usd": 0.0}
    monkeypatch.setattr(engine.sam, "segment", segment)
    step = {"objects": [{"name": n, "sam_prompt": n, "size": None} for n in ("red wire", "blue wire", "white wire")],
            "checks": []}
    asyncio.run(engine.run(step, b"", "full"))
    assert sorted(started) == ["blue wire", "red wire", "white wire"]


def test_provider_wiring(monkeypatch):
    """Live sessions use the engine only with a key; replay always stays on the recording; /api/config says which."""
    from fastapi.testclient import TestClient

    from server import providers
    from server.main import app
    c = TestClient(app)
    assert c.get("/api/config").json()["live_checks"] == {"source": "fixture", "reason": "MODEL_API_KEY is not set"}
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    assert c.get("/api/config").json()["live_checks"]["source"] == "sam"
    ran = []

    async def fake_run(step, frame, kind, session_id=None, end_frame=None, end_box=None):
        assert end_frame[:2] == b"\xff\xd8"  # the step's end frame JPEG
        refs = json.loads((providers.DATA / "tutorials/install-thermostat/refs/step-4.json").read_text())
        assert end_box and end_box[0] < end_box[2] and end_box[1] < end_box[3]  # the step-4 parts' box on its end frame
        assert any(o.get("box") for o in refs["objects"])  # (from refs/step-4.json)
        ran.append(step["id"])
        return {"verdict": "done"}
    monkeypatch.setattr(providers.engine, "run", fake_run)
    meta = json.dumps({"step_id": 4, "kind": "full", "check_no": 1, "trigger": "check_me", "trigger_at": 0})
    for mode, extra in (("live", {}), ("replay", {"recording_id": "with-mistakes"})):
        sid = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": mode, "age_confirmed": True,
                                            **extra}).json()["session_id"]
        r = c.post(f"/api/sessions/{sid}/checks", files={"frame": ("f.jpg", b"\xff\xd8\xff", "image/jpeg")},
                   data={"meta": meta})
        assert r.status_code == 200
    assert ran == [4]  # the replay session never reached the engine
    monkeypatch.setattr(providers, "PROVIDER", "cached")
    assert c.get("/api/config").json()["live_checks"] == {"source": "fixture", "reason": "FA_PROVIDER=cached"}


def test_size_filter_runs_before_the_measured_checks(monkeypatch):
    """FR-44: Joy-Con step 2 counts 4 shell screws even when SAM also outlines the empty screw holes and the spare
    stick (as "circle" does on the stand-in frame); the filter applies before `count` and `inside`."""
    plan = json.loads((Path(__file__).parent / "fixtures/stand-in-joycon-plan.json").read_text(encoding="utf-8"))
    step2 = plan["steps"][1]
    screws = [inst([x, y, x + 26, y + 26]) for x, y in ((817, 219), (937, 219), (1057, 219), (817, 349))]  # 0.00073
    holes = [inst([389 + 180 * i, 130, 409 + 180 * i, 150]) for i in range(2)]   # 21 x 21 = 0.00048
    stick = [inst([1144, 584, 1256, 696])]
    tray = [inst([760, 149, 1141, 569])]
    res, _ = run(monkeypatch, step2, {"screw": screws + holes + stick, "parts tray": tray})
    assert res["verdict"] == "done"
    assert res["results"][0] == {"check_id": "s2c1", "type": "count", "kind": "measured", "pass": True, "value": 4,
                                 "threshold": 4}
    res, _ = run(monkeypatch, {**step2, "objects": [{**o, "min_area": None, "max_area": None} for o in step2["objects"]]},
                 {"screw": screws + holes + stick, "parts tray": tray})
    assert res["verdict"] == "wrong" and res["hint"] == "I count 7 of the screw, not 4."


def test_terminal_screws_outside_the_size_band_are_dropped(monkeypatch):
    """A bigger stray circle (a mounting hole) next to the six terminal screws doesn't make the count 7."""
    res, _ = run(monkeypatch, STEP[7], {"circle": SCREWS + [inst([100, 500, 199, 599])], **WIRES})
    assert res["verdict"] == "wrong" and res["hint"] == "Blue wire goes in C, not Rh. Rh is 24 V power — this would blow your furnace fuse."


def test_hand_gate_parallel_logs_a_warning_at_startup(tmp_path):
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", "import server.main"], capture_output=True, text=True,
                         env={"FA_HAND_GATE_PARALLEL": "true", "PATH": "", "FA_DATA_DIR": str(tmp_path),
                              # Windows needs SYSTEMROOT to start asyncio
                              **{k: os.environ[k] for k in ("SYSTEMROOT",) if k in os.environ}},
                         cwd=Path(__file__).parent.parent)
    assert out.returncode == 0, out.stderr
    assert "FA_HAND_GATE_PARALLEL" in out.stderr and "NFR-13" in out.stderr


def test_any_colour_rope_but_the_right_colour_wire():
    """A learner's rope, shirt or LED can be any colour; a wire's colour stays strict when the step has two colours of it."""
    from server.checks.engine import loose
    rope = {"objects": [{"name": "blue rope", "sam_prompt": "blue rope"}]}
    wires = {"objects": [{"name": "red wire", "sam_prompt": "red wire"}, {"name": "white wire", "sam_prompt": "white wire"}]}
    assert loose("blue rope", rope) == "rope"
    assert loose("red wire", wires) is None and loose("white wire", wires) is None
    assert loose("rope", rope) is None  # nothing to drop


def test_a_thin_cord_is_still_the_rope():
    from server.checks.engine import alternatives
    rope = {"objects": [{"name": "blue rope", "sam_prompt": "blue rope"}]}
    wires = {"objects": [{"name": "red wire", "sam_prompt": "red wire"}, {"name": "white wire", "sam_prompt": "white wire"}]}
    assert alternatives("blue rope", rope) == ["rope", "string", "cord", "knot"]
    assert alternatives("red wire", wires) == []  # colour tells the wires apart: never looser


def test_wiring_colours_are_never_relaxed_and_blank_prompts_are_safe():
    from server.checks.engine import alternatives
    ground = {"instruction": "Twist the green ground wire to the bare copper.", "objects": [{"name": "green wire", "sam_prompt": "green wire"}],
              "checks": [{"text": "The green wire is inside the ground cap"}]}
    assert alternatives("green wire", ground) == []
    assert alternatives("", {}) == [] and alternatives("   ", {}) == []
