"""Abuse from a script that drops its cookie: body sizes, event logs, the shared creator view, slots held by heartbeats."""

from fastapi.testclient import TestClient

from server import providers
from server.main import app

from test_api import session
from test_insights import learner, write_logs
from test_persistence import hosted  # noqa: F401 (fixture)


def test_request_bodies_are_capped(scripted):
    c = TestClient(app)
    sid = session(c, mode="replay", recording_id="with-mistakes")
    big = {"type": "x", "data": {"pad": "a" * 100_000}}
    assert c.post(f"/api/sessions/{sid}/events", json=big).status_code == 413  # by Content-Length
    chunked = c.post(f"/api/sessions/{sid}/events", content=iter([b"{" + b" " * 70_000 + b"}"]))
    assert chunked.status_code == 413  # no Content-Length: counted while reading
    assert c.post(f"/api/sessions/{sid}/events", json={"type": "x", "data": {"pad": "a" * 3000}}).status_code == 400
    assert c.post(f"/api/sessions/{sid}/events", json={"type": "x", "data": "text"}).status_code == 400


def test_events_take_the_server_clock(scripted):
    c = TestClient(app)
    sid = session(c, mode="replay", recording_id="with-mistakes")
    assert c.post(f"/api/sessions/{sid}/events", json={"type": "x", "at": "not-a-date", "step_id": 2}).status_code == 204
    assert providers.sessions.events(sid)[-1]["at"].startswith("20")


def test_creator_view_survives_bad_logs(tmp_path, monkeypatch):
    bad = learner(wrong_at_4=True)
    bad[-2]["at"] = "not-a-date"
    spam = learner()
    spam.insert(1, {**spam[1], "type": "mistake_shown", "step_id": 4, "data": {"failed_checks": ["Visit evil.example", []]}})
    write_logs(tmp_path, [learner(), learner(), learner(wrong_at_4=True), bad, spam])
    monkeypatch.setattr(providers, "insights", providers.Insights(tmp_path, tmp_path / "reports"))
    view = TestClient(app).get("/api/creator/install-thermostat").json()
    assert view["sessions"] == 4  # the unreadable log is skipped
    assert {r["step_id"]: r for r in view["steps"]}[4]["top_mistakes"] == ["The supply black wire end is inside the red nut"]


def test_heartbeats_dont_hold_a_slot_forever(hosted, scripted):  # noqa: F811
    a, b, c3 = TestClient(app), TestClient(app), TestClient(app)
    sa, sb = session(a), session(b)
    for minute in (9, 18, 27, 36):  # a heartbeats every 9 minutes, never idle
        hosted.t = hosted.t.replace(minute=minute)
        a.post(f"/api/sessions/{sa}/events", json={"type": "heartbeat"})
        b.post(f"/api/sessions/{sb}/events", json={"type": "heartbeat"})
    session(c3)  # both are past FA_SESSION_MAX_MINUTES (30), so a slot is free
    assert [e["data"]["reason"] for e in providers.sessions.events(sa) if e["type"] == "session_end"] == ["expired"]


def test_uploads_queue_per_visitor(monkeypatch, mp4):
    """One upload per visitor; FA_MAX_CONCURRENT_UPLOADS run at once, the rest wait their turn; a full queue refuses."""
    import asyncio
    import time
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    monkeypatch.setitem(providers.UPLOAD, "jobs", 3)
    monkeypatch.setattr(providers.tutorials, "slots", asyncio.Semaphore(2))
    started = []

    async def slow(src, out, tid, info, progress):
        started.append(tid)
        await asyncio.sleep(30)
    monkeypatch.setattr(providers.processing, "process", slow)
    with TestClient(app) as client:  # one event loop, like the server; each cookie is another visitor's machine
        def as_(who):
            client.cookies.set("fa_visitor", who)
            return client

        def up(who):
            return as_(who).post("/api/tutorials", files={"file": ("w.mp4", mp4, "video/mp4")})

        def wait_started(n):
            for _ in range(50):
                if len(started) == n:
                    return
                time.sleep(0.02)
        ids = [up(who).json()["id"] for who in "abc"]  # three visitors: all accepted
        assert up("a").json()["error"]["message"].startswith("Your last upload")  # one at a time per visitor
        assert up("d").json()["error"]["message"].startswith("Lots of people")  # the queue is full
        wait_started(2)
        assert started == ids[:2] and as_("c").get(f"/api/tutorials/{ids[2]}").json()["progress"] == "queued 0/0"
        as_("a").delete(f"/api/tutorials/{ids[0]}")  # a slot frees up: the queued one starts
        wait_started(3)
        assert started == ids and as_("c").get(f"/api/tutorials/{ids[2]}").json()["progress"] == "finding_steps 0/0"
        for tid, who in zip(ids[1:], "bc"):
            as_(who).delete(f"/api/tutorials/{tid}")


def test_same_video_opens_the_existing_tutorial(mp4, scripted):
    """An upload with the same bytes as a bundled tutorial, or as the visitor's own earlier upload, isn't processed
    again (no cost, no daily upload used); another visitor's upload is never matched."""
    import hashlib
    c = TestClient(app)
    bundled = (providers.DATA / "tutorials/light-switch/video.mp4").read_bytes()
    assert hashlib.sha256(bundled).hexdigest() in providers.tutorials.bundled_hashes
    r = c.post("/api/tutorials", files={"file": ("again.mp4", bundled, "video/mp4")})
    assert r.status_code == 200 and r.json() == {"id": "light-switch", "status": "ready", "duplicate": True}
    first = c.post("/api/tutorials", files={"file": ("mine.mp4", mp4, "video/mp4")}).json()["id"]
    r = c.post("/api/tutorials", files={"file": ("mine-again.mp4", mp4, "video/mp4")})
    assert r.json()["id"] == first and r.json()["duplicate"] is True
    other = TestClient(app).post("/api/tutorials", files={"file": ("theirs.mp4", mp4, "video/mp4")}).json()
    assert other["id"] != first and "duplicate" not in other
    assert len([d for d in (providers.RUNTIME / "tutorials").iterdir() if d.name in (first, other["id"])]) == 2
