"""Every route answers with the documented shape (fixture provider). Run: .venv/bin/pytest tests"""

import json

import pytest
from fastapi.testclient import TestClient

from server import providers, recordings
from server.main import CSP, app

pytestmark = pytest.mark.usefixtures("scripted")  # live sessions on the fixture script, no key

TUTORIAL_KEYS = {"id", "title", "source", "owner_visitor", "creator", "licence", "duration_s", "step_count",
                 "status", "progress", "created_at", "delete_after"}
CHECK_RESULT_KEYS = {"verdict", "fallback", "results", "hint", "objects", "hand_blocked", "latency_ms", "cost_usd", "models"}
SUMMARY_KEYS = {"steps_done", "steps_total", "duration_s", "mistakes", "hardest_step_id", "rewinds"}


@pytest.fixture
def c():
    return TestClient(app)


def session(c, mode="live", **extra):
    body = {"tutorial_id": "install-thermostat", "mode": mode, "age_confirmed": True, **extra}
    r = c.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def check(c, sid, step, n, kind="full", force=""):
    meta = {"step_id": step, "kind": kind, "check_no": n, "trigger": "check_me", "trigger_at": 0}
    url = f"/api/sessions/{sid}/checks" + (f"?force={force}" if force else "")
    return c.post(url, files={"frame": ("f.jpg", b"\xff\xd8\xff", "image/jpeg")}, data={"meta": json.dumps(meta)})


def test_health_config_and_headers(c):
    r = c.get("/healthz")
    assert r.json() == {"ok": True, "live_enabled": True}
    assert r.headers["content-security-policy"] == CSP
    assert "fa_visitor" in r.cookies
    assert c.get("/js/main.js").headers["cache-control"] == "no-cache"  # web app files revalidate after a deploy
    cfg = c.get("/api/config").json()
    assert {"live_enabled", "hosted", "voice", "limits", "motion", "caps_remaining_day_usd"} <= cfg.keys()
    assert {"upload_max_s", "sessions_per_day", "uploads_per_day"} <= cfg["limits"].keys()


def test_ut72_no_key_means_replay_only(c, monkeypatch, mp4):
    """FR-152 (DEF-002): FA_PROVIDER=live without a key never runs the scripted checks as if they were real."""
    monkeypatch.setattr(providers, "PROVIDER", "live")
    assert c.get("/healthz").json()["live_enabled"] is False and c.get("/api/config").json()["live_enabled"] is False
    r = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True})
    assert r.status_code == 503 and r.json()["error"]["message"].startswith("Add a Meta API key")
    assert c.post("/api/tutorials", files={"file": ("x.mp4", mp4, "video/mp4")}).status_code == 503
    sid = session(c, "replay", recording_id="with-mistakes", age_confirmed=False)
    assert check(c, sid, 4, 1).json()["verdict"] == "wrong"  # replay still works
    monkeypatch.setattr(providers, "PROVIDER", "cached")  # a live session started before the key went away
    live = session(c)
    monkeypatch.setattr(providers, "PROVIDER", "live")
    assert check(c, live, 4, 1).json()["error"]["code"] == "no_api_key"


def test_tutorials_and_plan(c):
    ts = c.get("/api/tutorials").json()
    assert [t["id"] for t in ts] == ["breadboard-led", "figure-eight-knot", "tie-a-tie", "solder-wires",
                                     "jump-start-car", "install-thermostat", "light-switch", "ethernet-crimp",
                                     "robot-pick-place"]  # real videos only, all CC BY
    assert all(t["licence"].startswith("CC BY") and t["source_url"].startswith("https://www.youtube.com/") for t in ts)
    assert all(t.keys() == TUTORIAL_KEYS | {"source_url", "source_title"} for t in ts) and all(t["creator"] for t in ts)
    assert c.get("/api/tutorials/install-thermostat").json()["step_count"] == 7
    plan = c.get("/api/tutorials/install-thermostat/plan").json()
    assert {"tutorial_id", "model", "transcript_used", "prompt_version", "plan_hash", "reviewed", "needs", "steps"} <= plan.keys()
    s4 = plan["steps"][3]
    assert {"id", "start", "end", "instruction", "done_looks_like", "end_frame", "objects", "checks",
            "common_mistakes", "key_clip_start", "key_clip_end"} <= s4.keys()
    assert [(ch["kind"], ch["type"], ch["a"], ch["text"]) for ch in s4["checks"]] == [  # a splice: measured + judged
        ("measured", "inside", "supply black wire", "The supply black wire end is inside the red nut"),
        ("judged", "judged", None, "The supply black wire is spliced to one thermostat lead under the red nut, yes?")]
    measured = {"present", "count", "inside", "adjacent", "left_of", "above", "area_ratio"}
    for tid in ("install-thermostat", "light-switch"):
        for st in c.get(f"/api/tutorials/{tid}/plan").json()["steps"]:
            assert all(ch["type"] in measured for ch in st["checks"] if ch["kind"] == "measured")
    assert c.put("/api/tutorials/install-thermostat/plan", json=plan).status_code == 200
    assert c.put("/api/tutorials/install-thermostat/plan", json={"tutorial_id": "x"}).json()["error"]["code"] == "invalid_plan"
    for typ in ("teleport", "touching", "judged"):  # DEF-006: a measured check needs a known measured type
        bad = json.loads(json.dumps(plan))
        bad["steps"][3]["checks"][0]["type"] = typ  # step 4: a measured check
        assert c.put("/api/tutorials/install-thermostat/plan", json=bad).status_code == 422
    assert c.delete("/api/tutorials/install-thermostat").json()["error"]["code"] == "forbidden"
    assert c.get("/api/tutorials/nope").json()["error"]["code"] == "not_found"


def test_media_video_range_and_frames(c):
    full = c.get("/api/tutorials/install-thermostat/video")
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes" and full.content[4:8] == b"ftyp"
    r = c.get("/api/tutorials/install-thermostat/video", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"
    assert r.headers["content-range"] == f"bytes 100-199/{len(full.content)}" and r.content == full.content[100:200]
    assert c.get("/api/config").json()["stand_in_media"] == []  # both bundled tutorials are real videos
    for url in ("/api/tutorials/install-thermostat/frames/4", "/api/tutorials/light-switch/frames/6",
                "/api/recordings/with-mistakes/frames/0"):
        r = c.get(url)
        assert r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff", url
    rec = recordings.DATA / "recordings/with-mistakes/frames"  # made by conftest.test_recordings
    frames = sorted(int(f.stem.split("-")[1]) for f in rec.glob("*.jpg"))
    def at(t):
        return c.get(f"/api/recordings/with-mistakes/frames/{t}").content

    def pick(ms):
        return (rec / f"preview-{ms}.jpg").read_bytes()
    assert at(frames[3]) == pick(frames[3]) and at(frames[3] + 1) == pick(frames[3])  # latest frame at or before t
    assert c.get("/api/recordings/with-mistakes/frames/abc").status_code == 404


def test_upload_progress_then_ready(c, monkeypatch, mp4):
    assert c.post("/api/tutorials", files={"file": ("x.mov", b"not a video", "video/mp4")}).json()["error"]["code"] == "invalid_upload"
    r = c.post("/api/tutorials", files={"file": ("my build.mp4", mp4, "video/mp4")})
    assert r.status_code == 202 and r.json()["status"] == "processing"
    tid = r.json()["id"]
    meta = c.get(f"/api/tutorials/{tid}").json()
    assert meta["status"] == "processing" and meta["progress"].startswith("finding_steps")
    assert tid in [t["id"] for t in c.get("/api/tutorials").json()]
    providers.tutorials.uploads[tid]["t0"] -= 10
    assert c.get(f"/api/tutorials/{tid}").json()["status"] == "ready"
    assert c.get(f"/api/tutorials/{tid}/plan").json()["tutorial_id"] == tid
    assert TestClient(app).get(f"/api/tutorials/{tid}").status_code == 404  # another browser
    assert c.delete(f"/api/tutorials/{tid}").status_code == 204


def test_live_session_scripted_checks(c):
    assert c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live"}).json()["error"]["code"] == "consent_required"
    sid = session(c)
    r = check(c, sid, 3, 1)
    assert r.json().keys() == CHECK_RESULT_KEYS and r.json()["hand_blocked"] is True
    wrong = check(c, sid, 4, 1).json()
    assert wrong["verdict"] == "wrong" and wrong["hint"].startswith("Your red wire isn't in Rc yet.")
    assert [x["check_id"] for x in wrong["results"] if not x["pass"]] == ["s4c1"]
    assert check(c, sid, 4, 2).json()["verdict"] == "done"
    assert check(c, sid, 4, 3, kind="recheck").json()["verdict"] == "done"
    assert check(c, sid, 1, 1).json()["verdict"] == "unsure"
    assert check(c, sid, 1, 1, kind="visibility").json()["objects"][0]["name"] == "old backplate"
    assert check(c, sid, 3, 1, force="sam_failed").json()["fallback"] == "sam_failed"
    assert check(c, sid, 3, 1, force="judge_failed").json()["verdict"] == "unsure"
    assert check(c, sid, 3, 1, force="cap_reached").status_code == 429
    assert TestClient(app).post(f"/api/sessions/{sid}/events", json={"type": "advance"}).status_code == 404  # other browser
    other = session(c, tutorial_id="light-switch")  # no script of its own: reuses the Nest one
    assert check(c, other, 4, 1).json()["verdict"] == "wrong"


def test_replay_session_and_summary(c):
    recs = c.get("/api/recordings").json()
    assert {r["id"] for r in recs} == {"clean", "with-mistakes"}
    assert {"id", "tutorial_id", "title", "who", "plan_hash", "prompt_version", "recorded_at"} <= recs[0].keys()
    lines = c.get("/api/recordings/with-mistakes/timeline").text.splitlines()
    events = [json.loads(x) for x in lines]
    assert {"session_id", "at", "type", "tutorial_id", "step_id", "mode", "data"} <= events[0].keys()
    assert c.get("/api/recordings/clean/frames/1000").headers["content-type"] == "image/jpeg"
    sid = session(c, "replay", recording_id="with-mistakes", age_confirmed=False)
    assert check(c, sid, 4, 1).json()["verdict"] == "wrong"
    assert check(c, sid, 4, 9).json()["error"]["code"] == "not_found"  # a cache miss is loud in replay
    for e in events:
        if e["type"] not in ("session_start", "session_end"):
            assert c.post(f"/api/sessions/{sid}/events", json=e).status_code == 204
    s = c.post(f"/api/sessions/{sid}/end", json={"reason": "completed"}).json()
    assert s.keys() == SUMMARY_KEYS
    assert s["steps_done"] == 7 and [m["step_id"] for m in s["mistakes"]] == [4]


def test_voice_creator_evaluation(c):
    sid = session(c)
    meta = json.dumps({"step_id": 4, "question": "what now?", "question_no": 1})
    r = c.post(f"/api/sessions/{sid}/questions", files={"frame": ("f.jpg", b"\xff\xd8\xff", "image/jpeg")}, data={"meta": meta})
    assert {"answer", "latency_ms"} <= r.json().keys()
    from tests.test_voice import make_wav
    assert "transcript" in c.post(f"/api/sessions/{sid}/transcribe", content=make_wav(1)).json()
    view = c.get("/api/creator/install-thermostat").json()
    assert {"sample", "sessions", "steps"} == view.keys()
    assert len(view["steps"]) == 7 and {"step_id", "sessions", "stuck_rate", "top_mistakes"} <= view["steps"][0].keys()
    ev = c.get("/api/evaluation/latest").json()
    assert {"run_at", "done_accuracy", "latency_ms_p50", "cost_per_session_usd"} <= ev.keys()


def test_errors_cross_origin_and_force(c):
    r = c.post("/api/sessions", json={}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "cross_origin"
    r = c.get("/api/tutorials?force=cap_reached")
    assert r.status_code == 429 and r.json() == {"error": {"code": "cap_reached", "message": r.json()["error"]["message"], "retry_after_s": 3600}}
    assert c.post("/api/sessions?force=limit_reached", json={}).json()["error"]["message"].startswith("You've used today's")


def test_allowed_origin_setting(c, monkeypatch):
    """FA_ALLOWED_ORIGIN: when set, state-changing requests must come from exactly that origin (a proxy's Host
    differs); unset, the request's own Host decides (any local port)."""
    from server import api
    body = {"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True}
    assert c.post("/api/sessions", json=body, headers={"origin": "http://testserver"}).status_code == 201
    monkeypatch.setattr(api, "ALLOWED_ORIGIN", "https://followalong.onrender.com")
    r = c.post("/api/sessions", json=body, headers={"origin": "http://testserver"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "cross_origin"
    assert c.post("/api/sessions", json=body, headers={"origin": "https://followalong.onrender.com"}).status_code == 201
    assert api.same_origin("https://followalong.onrender.com", None, "internal:10000")  # the voice WebSocket
    assert not api.same_origin("https://evil.example", None, "followalong.onrender.com")
    monkeypatch.setattr(api, "ALLOWED_ORIGIN", None)
    assert api.same_origin("http://127.0.0.1:8080", "http", "127.0.0.1:8080")
    assert api.same_origin("http://localhost:8000", None, "localhost:8000")
    assert not api.same_origin("http://localhost:9999", None, "localhost:8000")


def test_privacy_and_terms_text_matches_the_legal_doc(c):
    """FR-31: the in-app pages show the legal doc's text word for word (placeholders included), served as static files."""
    from pathlib import Path
    src = Path(__file__).parent.parent.parent / "documentation/08-legal/01-terms-and-privacy-notice.md"
    if not src.exists():
        pytest.skip("legal source doc is only in the working repo")
    doc = src.read_text(encoding="utf-8")
    sections = {p.strip().split("\n")[0]: p.strip() + "\n" for p in doc.split("\n---\n")}
    for name, heading in (("terms", "## Terms of use"), ("privacy", "## Privacy notice")):
        r = c.get(f"/legal/{name}.md")
        assert r.status_code == 200 and r.text == sections[heading]
        assert "github.com/unnatibshah/followalong/issues" in r.text
