"""UT-64-69, 73, 74 (caps, counters, limits, hosted refusal), persistence across a restart, the file lock, and the
recording provider's output replayed by the cached provider (FR-142)."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from server import providers, recordings
from server.gateway import Budget, CapReached, Gateway
from server.main import app
from server.store import ET

from conftest import make_video
from test_api import check, session

pytestmark = pytest.mark.usefixtures("scripted")  # live sessions on the fixture script, no key

CAPS = {"session": 1.5, "day": 10, "total": 50}
LIMITS = {"sessions_per_day": 2, "uploads_per_day": 1, "concurrent": 2, "idle_s": 600, "max_age_s": 1800,
          "ip_sessions_per_day": 10}


class Clock:
    def __init__(self, iso):
        self.t = datetime.fromisoformat(iso).astimezone(ET)

    def __call__(self):
        return self.t


def test_ut64_65_caps_before_the_call(tmp_path):
    b = Budget(tmp_path / "budget.json", CAPS, enforce=True)
    b.charge("s1", 1.45)
    with pytest.raises(CapReached) as e:
        b.check("s1", 0.10)
    assert e.value.which == "session"
    b.check("s2", 0.10)  # another session is fine
    b.charge("s2", 8.54)  # day now 9.99
    with pytest.raises(CapReached) as e:
        b.check("s3", 0.10)
    assert e.value.which == "day" and e.value.retry_after_s > 0
    b2 = Budget(tmp_path / "b2.json", CAPS, enforce=True)
    b2.charge(None, 49.99)
    b2.clock = Clock("2030-01-01T12:00:00+00:00")  # a new day: only the total is left
    with pytest.raises(CapReached) as e:
        b2.check(None, 0.10)
    assert e.value.which == "total" and e.value.retry_after_s is None
    Budget(tmp_path / "budget.json", CAPS, enforce=False).check("s1", 100)  # local: counted, never refused


def test_gateway_refuses_capped_call_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    calls = []
    gw = Gateway(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={})),
                 budget=Budget(tmp_path / "b.json", {**CAPS, "session": 0.001}, enforce=True))
    with pytest.raises(CapReached):
        asyncio.run(gw.responses({"model": "sam-3.1", "input": []}, 8, "s"))
    assert calls == []


def test_ut66_counters_persist_and_ut74_processing(tmp_path):
    Budget(tmp_path / "budget.json", CAPS, enforce=True).charge(None, 0.50)  # tutorial processing: no session
    Budget(tmp_path / "budget.json", CAPS, enforce=True).charge("s1", 0.25)
    b = Budget(tmp_path / "budget.json", CAPS, enforce=True).snapshot()  # "after a restart"
    assert b["day_usd"] == 0.75 and b["total_usd"] == 0.75 and b["sessions"] == {"s1": 0.25} and b["caps"] == CAPS


def test_file_lock_counts_every_concurrent_write(tmp_path):
    b = Budget(tmp_path / "budget.json", CAPS, enforce=False)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: b.charge(f"s{i % 4}", 0.01), range(200)))
    snap = b.snapshot()
    assert snap["total_usd"] == pytest.approx(2.0) and sum(snap["sessions"].values()) == pytest.approx(2.0)


def test_ut67_day_rollover_et(tmp_path):
    clock = Clock("2026-11-06T04:59:00+00:00")  # 23:59 ET on Nov 5
    b = Budget(tmp_path / "budget.json", CAPS, enforce=True, clock=clock)
    v = providers.Visitors(tmp_path / "visitors.json", LIMITS, enforce=True, clock=clock)
    b.charge("s1", 2.0)
    v.admit_upload("cookie")
    clock.t = datetime.fromisoformat("2026-11-06T05:01:00+00:00").astimezone(ET)  # 00:01 ET on Nov 6
    snap = b.snapshot()
    assert snap["day"] == "2026-11-06" and snap["day_usd"] == 0 and snap["total_usd"] == 2.0
    v.admit_upload("cookie")  # yesterday's upload no longer counts


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    """Hosted limits and caps, on throwaway files, answered from the with-mistakes recording (as SIT-08)."""
    clock = Clock("2026-11-06T15:00:00+00:00")
    monkeypatch.setattr(providers, "visitors", providers.Visitors(tmp_path / "visitors.json", LIMITS, True, clock))
    monkeypatch.setattr(providers.gateway, "budget", Budget(tmp_path / "budget.json", dict(CAPS), True, clock))
    monkeypatch.setattr(providers, "CACHED_RECORDING", "with-mistakes")
    return clock


def test_ut68_visitor_limits(hosted, mp4):
    c = TestClient(app)
    session(c), session(c)
    r = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True})
    assert r.status_code == 429 and r.json()["error"]["code"] == "limit_reached"
    assert r.json()["error"]["message"].startswith("You've used today's 2 live sessions")
    assert session(c, mode="replay", recording_id="with-mistakes")  # replay is never limited
    assert c.post("/api/tutorials", files={"file": ("a.mp4", mp4, "video/mp4")}).status_code == 202
    r = c.post("/api/tutorials", files={"file": ("b.mp4", make_video(3), "video/mp4")})  # a different video
    assert r.status_code == 429 and r.json()["error"]["code"] == "limit_reached"


def test_ut69_concurrency_and_expiry(hosted):
    a, b, c3 = TestClient(app), TestClient(app), TestClient(app)
    sa, sb = session(a), session(b)
    r = c3.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True})
    assert r.json()["error"]["message"].startswith("2 live sessions are already running")
    hosted.t = hosted.t.replace(minute=5)
    a.post(f"/api/sessions/{sa}/events", json={"type": "heartbeat"})  # a keeps its tab open
    hosted.t = hosted.t.replace(minute=11)  # b has been idle 11 minutes, a 6
    session(c3)
    assert b.post(f"/api/sessions/{sb}/end", json={}).json()  # still answers with a summary
    ends = [e for e in providers.sessions.events(sb) if e["type"] == "session_end"]
    assert [e["data"]["reason"] for e in ends] == ["expired"]
    assert not any(e["type"] == "heartbeat" for e in providers.sessions.events(sa))


def test_sit08_caps_through_the_api(hosted):
    providers.gateway.budget.caps.update(session=0.02, day=0.04)
    c = TestClient(app)
    sid = session(c)
    assert check(c, sid, 6, 1).status_code == 200  # $0.0166 from the recording (step 6)
    r = check(c, sid, 6, 2)
    assert r.status_code == 429 and r.json()["error"]["code"] == "cap_reached" and "retry_after_s" not in r.json()["error"]
    providers.gateway.budget.caps["session"] = 1.0
    sid2 = session(c)
    assert check(c, sid2, 6, 1).status_code == 200
    r = check(c, sid2, 6, 2)  # day at $0.0332: the next $0.0166 crosses $0.04
    assert r.json()["error"]["code"] == "cap_reached" and r.json()["error"]["retry_after_s"] > 0
    assert c.get("/api/config").json()["caps_remaining_day_usd"] == pytest.approx(0.0068)


def test_sessions_and_plan_edits_survive_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "RUNTIME", tmp_path)
    monkeypatch.setattr(providers, "sessions", providers.FileSessions(tmp_path / "sessions"))
    c = TestClient(app)
    sid = session(c)
    c.post(f"/api/sessions/{sid}/events", json={"type": "advance", "step_id": 1})
    plan = c.get("/api/tutorials/install-thermostat/plan").json()
    plan["steps"][0]["instruction"] = "Edited"
    assert c.put("/api/tutorials/install-thermostat/plan", json=plan).status_code == 200
    # "restart": fresh providers reading the same folder
    monkeypatch.setattr(providers, "sessions", providers.FileSessions(tmp_path / "sessions"))
    monkeypatch.setattr(providers, "tutorials", providers.FixtureTutorials())
    assert c.post(f"/api/sessions/{sid}/end", json={"reason": "left"}).json()["steps_done"] == 1
    assert c.get("/api/tutorials/install-thermostat/plan").json()["steps"][0]["instruction"] == "Edited"
    assert TestClient(app).get("/api/tutorials/install-thermostat/plan").json()["steps"][0]["instruction"] != "Edited"
    assert TestClient(app).post(f"/api/sessions/{sid}/end", json={}).status_code == 404  # another browser
    log = (tmp_path / "sessions" / f"{sid}.jsonl").read_text(encoding="utf-8")
    assert "fa_visitor" not in log and c.cookies["fa_visitor"] not in log  # only its hash


def test_ut73_recording_refused_when_hosted():
    env = {**os.environ, "FA_HOSTED": "true", "FA_PROVIDER": "recording"}
    r = subprocess.run([sys.executable, "-c", "import server.config"], env=env, capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(__file__)))
    assert r.returncode != 0 and "refused" in r.stderr
    env = {**os.environ, "FA_HOSTED": "true", "FA_CACHED_RECORDING": "with-mistakes"}
    r = subprocess.run([sys.executable, "-c", "import server.config"], env=env, capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(__file__)))
    assert r.returncode != 0


def test_recording_round_trips_through_replay(tmp_path, monkeypatch):
    """FA_PROVIDER=recording on a live session (fixture script, no key), promote it, replay it: same verdicts."""
    monkeypatch.setattr(providers, "PROVIDER", "recording")
    monkeypatch.setattr(recordings, "ROOT", tmp_path / "rec")
    fixtures = tmp_path / "fixtures"
    (fixtures / "recordings").mkdir(parents=True)
    monkeypatch.setattr(recordings, "BUNDLED", fixtures / "recordings")
    monkeypatch.setattr(recordings, "DATA", tmp_path / "data")
    monkeypatch.setattr(providers, "FIXTURES", fixtures)
    monkeypatch.setattr(providers, "DATA", tmp_path / "data")
    c = TestClient(app)
    sid = session(c)
    c.post(f"/api/sessions/{sid}/events", json={"type": "step_play", "step_id": 1, "data": {"reason": "first"}})
    live = [check(c, sid, 1, 1).json(), check(c, sid, 3, 1).json()]
    c.post(f"/api/sessions/{sid}/end", json={"reason": "left"})
    rid = recordings.rec_id(sid)
    src = tmp_path / "rec" / rid
    assert {f.name for f in src.iterdir()} == {"meta.json", "timeline.jsonl", "results.jsonl", "frames"}
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    assert meta["plan_hash"].startswith("sha256:") and "consent_ref" in meta
    assert len(list((src / "frames").glob("check-*.jpg"))) == 2
    recordings.promote(rid, "tmp-test", "Test run", "consent-v1.pdf", force=False)
    assert c.get("/api/recordings").json()[0]["consent_ref"] == "consent-v1.pdf"
    types = [json.loads(line)["type"] for line in c.get("/api/recordings/tmp-test/timeline").text.splitlines()]
    assert types[0] == "session_start" and types[-1] == "session_end"
    rs = session(c, mode="replay", recording_id="tmp-test")
    assert [check(c, rs, 1, 1).json(), check(c, rs, 3, 1).json()] == live
    assert c.get("/api/recordings/tmp-test/frames/999999").content[:3] == b"\xff\xd8\xff"
    with pytest.raises(SystemExit):
        recordings.promote(rid, "tmp-test", None, None, force=False)


def test_cap_reserve_is_atomic(tmp_path):
    """Check-and-reserve in one locked write: 20 concurrent calls at $0.10 under a $1.00 cap admit exactly 10, and
    settling replaces each estimate with the real cost (a failed call refunds it)."""
    b = Budget(tmp_path / "budget.json", {**CAPS, "session": 1.0}, enforce=True)

    def call(_):
        try:
            b.reserve("s1", 0.10)
            return True
        except CapReached:
            return False
    with ThreadPoolExecutor(8) as pool:
        admitted = sum(pool.map(call, range(20)))
    assert admitted == 10 and b.snapshot()["sessions"]["s1"] == pytest.approx(1.0)
    b.settle("s1", 0.10, 0.04)
    b.settle("s1", 0.10, 0.0)
    assert b.snapshot()["sessions"]["s1"] == pytest.approx(0.84)
    b.reserve("s1", 0.10)  # room again after the refund


def test_ut77_plan_pinning(tmp_path, monkeypatch):
    """UT-77: a replay (or FA_CACHED_RECORDING) refuses a recording whose plan_hash or prompt_version doesn't match;
    bundled plans carry the hash of their content, so the bundled replays match."""
    monkeypatch.setattr(providers, "RUNTIME", tmp_path)
    c = TestClient(app)
    plan = c.get("/api/tutorials/install-thermostat/plan").json()
    assert plan["plan_hash"] == providers.recordings.meta("with-mistakes")["plan_hash"]
    assert session(c, mode="replay", recording_id="with-mistakes")
    plan["steps"][0]["instruction"] = "Edited"
    edited = c.put("/api/tutorials/install-thermostat/plan", json=plan).json()
    assert edited["plan_hash"] != providers.recordings.meta("with-mistakes")["plan_hash"]  # recomputed on save
    r = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "replay", "recording_id": "with-mistakes"})
    assert r.status_code == 404 and "different step plan" in r.json()["error"]["message"]
    monkeypatch.setattr(providers, "CACHED_RECORDING", "with-mistakes")  # live sessions answered from it: same rule
    r = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True})
    assert r.status_code == 404 and "different step plan" in r.json()["error"]["message"]
    monkeypatch.setattr(providers, "JUDGED_PROMPT", {"version": "judged_checks-9.9"})
    c.put("/api/tutorials/install-thermostat/plan", json={**plan, "steps": c.get("/api/tutorials/install-thermostat/plan").json()["steps"]})
    r = TestClient(app).post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True})
    assert r.status_code == 404  # live answers from a recording: plan and prompt must both match
    r = TestClient(app).post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "replay",
                                                    "recording_id": "with-mistakes"})
    assert r.status_code == 201  # a replay plays recorded answers: a newer prompt doesn't change them
