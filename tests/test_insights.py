"""UT-120-123: the creator view from session logs (FR-130, FR-131) and the evaluation report on S13 (FR-141, FR-143)."""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from server import evaluate, providers
from server.main import app

T0 = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)


def ev(t: float, type_: str, step=None, mode="live", **data) -> dict:
    at = (T0 + timedelta(seconds=t)).isoformat().replace("+00:00", "Z")
    return {"session_id": "x", "at": at, "type": type_, "tutorial_id": "install-thermostat", "step_id": step,
            "mode": mode, "data": data}


def write_logs(folder, logs):
    folder.mkdir(parents=True, exist_ok=True)
    for i, events in enumerate(logs):
        (folder / f"s{i}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def learner(wrong_at_4=False, slow_at_3=False, mode="live"):
    """A session log on the Nest tutorial (6 steps)."""
    e = [ev(0, "session_start", mode=mode, plan_hash="h")]
    for n, t in zip(range(1, 7), range(10, 1000, 100)):
        e += [ev(t, "step_play", n, mode=mode, reason="advance"), ev(t + 20, "step_wait", n, mode=mode)]
        if n == 4 and wrong_at_4:
            e += [ev(t + 25, "check", n, mode=mode, verdict="wrong", kind="full"),
                  ev(t + 26, "mistake_shown", n, mode=mode, failed_checks=["s4c1"])]
        e.append(ev(t + (20 + 130 if n == 3 and slow_at_3 else 40), "advance", n, mode=mode))
    return e + [ev(1000, "session_end", mode=mode, reason="completed")]


def test_ut120_creator_rows_from_session_logs(tmp_path, monkeypatch):
    """FR-130: stuck = a `wrong` at the step or > 2 min from its first step_wait to its advance; mistakes as check texts."""
    write_logs(tmp_path, [learner(wrong_at_4=True), learner(slow_at_3=True), learner(), learner(mode="replay")])
    monkeypatch.setattr(providers, "insights", providers.Insights(tmp_path, tmp_path / "reports"))
    view = TestClient(app).get("/api/creator/install-thermostat").json()
    assert view["sample"] is False and view["sessions"] == 3  # the replay isn't a learner
    rows = {r["step_id"]: r for r in view["steps"]}
    assert rows[4] == {"step_id": 4, "sessions": 3, "stuck_rate": 0.33, "top_mistakes": ["The supply black wire end is inside the red nut"]}
    assert rows[3]["stuck_rate"] == 0.33 and rows[3]["top_mistakes"] == []
    assert rows[1]["stuck_rate"] == 0.0 and len(rows) == 7


def test_ut121_creator_view_is_aggregates_only(tmp_path, monkeypatch):
    """FR-131: the view reads the logs only; no session ids, timestamps or files come out of it."""
    write_logs(tmp_path, [learner(wrong_at_4=True) for _ in range(3)])
    monkeypatch.setattr(providers, "insights", providers.Insights(tmp_path, tmp_path / "reports"))
    before = sorted(p.name for p in tmp_path.rglob("*"))
    body = TestClient(app).get("/api/creator/install-thermostat").text
    assert "s0" not in body and "2026-11-06" not in body and '"at"' not in body
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_ut122_creator_sample_until_three_sessions(tmp_path, monkeypatch):
    write_logs(tmp_path, [learner(), learner()])
    monkeypatch.setattr(providers, "insights", providers.Insights(tmp_path, tmp_path / "reports"))
    view = TestClient(app).get("/api/creator/install-thermostat").json()
    assert view["sample"] is True and len(view["steps"]) == 7  # one illustrative row per step, labelled sample
    plan = TestClient(app).get("/api/tutorials/install-thermostat/plan").json()
    for r, st in zip(view["steps"], plan["steps"]):
        assert 0 <= r["stuck_rate"] <= 0.45 and r["top_mistakes"] == st["common_mistakes"][:2]


def row(step, truth, verdict, latency=1000, cost=0.0075, checks=None, results=(), **label):
    return {"label": {"frame": f"f{step}.jpg", "tutorial_id": "install-thermostat", "step_id": step, "truth": truth,
                      "checks": checks, **label},
            "result": {"verdict": verdict, "fallback": None, "results": list(results), "latency_ms": latency,
                       "cost_usd": cost, "stages_ms": {"hand": 300}}}


def test_ut123_evaluation_report_and_s13(tmp_path, monkeypatch):
    """FR-141: every metric from labelled results; FR-143: the newest report is what /api/evaluation/latest serves."""
    m = {"check_id": "s4c1", "kind": "measured", "pass": True}
    rows = [row(4, "done", "done", 800, checks={"s4c1": True}, results=[m], lighting="lamp"),
            row(4, "not_done", "done", 900),                                  # a wrong advance
            row(7, "wrong", "wrong", 1200, mistake="Blue wire in the Rh terminal"),
            row(5, "done", "unsure", 4000),
            row(5, "done", "wrong", 2000, split="tuning")]                    # not in the reported numbers
    rep = evaluate.report(rows, "2 of 2", "test frames", 0.03)
    assert rep["label_count"] == 5 and rep["held_out_count"] == 4 and rep["tuning_count"] == 1
    assert rep["done_accuracy"] == "2 of 4" and rep["done_accuracy_ci"] == evaluate.wilson(2, 4)
    assert rep["done_accuracy_measured"] == "1 of 1 (100%)" and rep["done_accuracy_judged"] is None
    assert rep["wrong_advances"] == "1 of 2" and rep["wrong_corrections"] == "0 of 2"
    assert rep["unsure_rate"] == "1 of 4 (25%)" and rep["mistake_catch_rate"] == "1 of 1"
    assert rep["latency_ms_p50"] == "1.1 s" and rep["latency_ms_p90"] == "4.0 s" and rep["cost_per_check_usd"] == "$0.0075"
    assert rep["groups"] == {"lamp": {"count": 1, "done_accuracy": "1 of 1"}} and len(rep["config_hash"]) == 8
    monkeypatch.setattr(providers, "insights", providers.Insights(tmp_path, tmp_path / "reports"))
    assert TestClient(app).get("/api/evaluation/latest").json()["placeholder"] is True
    old = evaluate.write({**rep, "run_at": "2026-01-01T10:00:00Z"}, tmp_path / "reports")
    new = evaluate.write(rep, tmp_path / "reports")
    assert old.name == "2026-01-01.json" and new != old
    assert TestClient(app).get("/api/evaluation/latest").json()["run_at"] == rep["run_at"]
    assert evaluate.wilson(0, 0) is None and evaluate.wilson(10, 10)[1] == 1.0


def test_evaluate_dry_run(tmp_path, capsys):
    labels = tmp_path / "labels.jsonl"
    labels.write_text("".join(json.dumps({"frame": f"data/tutorials/install-thermostat/frames/step-{n}.jpg",
                                          "tutorial_id": "install-thermostat", "step_id": n, "truth": "done"}) + "\n"
                              for n in (2, 4)))
    evaluate.main(["--labels", str(labels), "--steps", str(tmp_path / "none-*.json"), "--dry-run"])
    assert "2 frames, estimated $" in capsys.readouterr().out


def test_step_plan_only(tmp_path, capsys):
    """No frame labels: score the step plan alone; a miss names the nearest planned step and how far off it is."""
    plan = json.loads(open("server/fixtures/plans/install-thermostat.json").read())
    first = plan["steps"][0]
    (tmp_path / "steps-x.json").write_text(json.dumps({"tutorial_id": "install-thermostat", "steps": [
        {k: first[k] for k in ("start", "end", "instruction")},
        {"start": first["start"] + 5, "end": first["end"] + 5, "instruction": "something else entirely"}]}))
    evaluate.main(["--steps", str(tmp_path / "steps-*.json")])
    out = capsys.readouterr().out
    assert f"step plan accuracy: 1 of {len(plan['steps'])}" in out and "Δstart -5.0 s" in out


def test_def018_evaluation_rescores_from_recorded_answers_without_calls(tmp_path, monkeypatch):
    """--live keeps every SAM 3.1 mask and Muse Spark answer; the default run re-scores from them with no calls."""
    import asyncio

    from server import sam, spark
    from test_engine import inst
    labels = [{"frame": "data/tutorials/install-thermostat/frames/step-6.jpg", "tutorial_id": "install-thermostat",
               "step_id": 6, "truth": "wrong"}]
    found = {"silver screw": [inst([720, 150, 750, 180])]}  # step 6: one screw, it wants two (a measured fail)

    async def segment(frame, concept, session_id=None):
        return {"concept": concept, "instances": found.get(concept, []), "size": (1280, 720), "latency_ms": 1, "cost_usd": 0.0025}

    async def judge(step, checks, frame, end_frame, box, session_id=None):
        return {"results": [{"check_id": "s6c2", "type": "judged", "kind": "judged", "pass": True, "confidence": 0.9,
                             "reason": "r"}], "hint": "h", "latency_ms": 5, "cost_usd": 0.002}
    monkeypatch.setattr(sam, "segment", segment)
    monkeypatch.setattr(spark, "judge", judge)
    live, kept = asyncio.run(evaluate.run(labels))
    evaluate.save_recorded(tmp_path / "rec.jsonl", kept)

    async def no_network(*a, **kw):
        raise AssertionError("called Meta")
    monkeypatch.setattr(sam, "segment", no_network)
    monkeypatch.setattr(spark, "judge", no_network)
    again, _ = asyncio.run(evaluate.run(labels, recorded=evaluate.load_recorded(tmp_path / "rec.jsonl")))
    assert [r["result"]["verdict"] for r in again] == [r["result"]["verdict"] for r in live] == ["wrong"]
    assert again[0]["result"]["results"] == live[0]["result"]["results"] and sam.segment is no_network  # restored
