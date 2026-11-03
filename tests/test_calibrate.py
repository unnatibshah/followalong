from server.calibrate import apply


def _c(cid, kind="measured", a=None, b=None, enabled=True):
    return {"id": cid, "kind": kind, "type": "inside" if kind == "measured" else "judged", "a": a, "b": b,
            "text": cid, "enabled": enabled, "needs_review": False}


def test_failing_and_missing_object_checks_are_disabled_and_goal_added_when_none_left():
    step = {"id": 1, "instruction": "Plug the wire in.", "done_looks_like": "Wire in the board.",
            "objects": [{"name": "wire"}, {"name": "board"}, {"name": "led"}],
            "checks": [_c("c1", a="wire", b="board"), _c("c2", a="led"), _c("c3", enabled=False)]}
    res = {"objects": [{"name": "wire", "found": True}, {"name": "board", "found": True}, {"name": "led", "found": False}],
           "results": [{"check_id": "c1", "kind": "measured", "pass": False, "value": 0.0}]}
    out = apply(step, res)
    assert [o["name"] for o in out["objects"]] == ["wire", "board"]
    assert [c["enabled"] for c in out["checks"][:3]] == [False, False, False]
    assert out["checks"][-1]["kind"] == "judged" and out["checks"][-1]["text"] == "Wire in the board."


def test_passing_checks_are_kept_and_no_goal_added():
    step = {"id": 2, "instruction": "x", "objects": [{"name": "wire"}],
            "checks": [_c("c1", a="wire"), _c("j1", kind="judged")]}
    res = {"objects": [{"name": "wire", "found": True}],
           "results": [{"check_id": "c1", "kind": "measured", "pass": True, "value": 1.0}]}
    assert apply(step, res) == step
