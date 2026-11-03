"""Launch-readiness pieces: the shared rate limit, safety tiers, the learner's language, Delete my data, retention,
metrics and browser errors."""

import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from server import i18n, providers, safety
from server.gateway import Busy, Metrics, RateLimiter
from server.main import app
from server.voice import command

pytestmark = pytest.mark.usefixtures("scripted")


@pytest.fixture
def c():
    return TestClient(app)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_rate_limit_sheds_coach_looks_first_and_queues_checks():
    clock = Clock()
    slept = []

    async def sleep(s):
        slept.append(s)
        clock.t += s

    lim = RateLimiter(10, background_share=0.7, max_wait_s=90, clock=clock, sleep=sleep)

    async def go():
        for _ in range(7):
            await lim.acquire("background")
        with pytest.raises(Busy):  # 70% used: a look is shed, never queued
            await lim.acquire("background")
        assert lim.shed == 1
        for _ in range(3):
            await lim.acquire("interactive")  # checks still get the last 30%
        assert lim.load() == 1.0 and lim.next_look_ms() == 6000
        await lim.acquire("interactive")  # full: waits for the oldest request to leave the minute
        assert slept and round(sum(slept)) == 60

    asyncio.run(go())


def test_rate_limit_gives_up_on_a_check_after_max_wait():
    clock = Clock()

    async def sleep(s):
        clock.t += s

    lim = RateLimiter(2, max_wait_s=5, clock=clock, sleep=sleep)

    async def go():
        await lim.acquire()
        await lim.acquire()
        with pytest.raises(Busy):
            await lim.acquire()

    asyncio.run(go())


def test_metrics_percentiles_and_errors():
    m = Metrics()
    for ms in (100, 200, 300, 400, 1000):
        m.add("sam-3.1", ms, 0.001, None)
    m.add("sam-3.1", 9000, 0.0, "timeout")
    s = m.summary()["sam-3.1"]
    assert s["calls"] == 6 and s["errors"] == 1 and s["p50_ms"] == 300 and s["p95_ms"] == 1000


@pytest.mark.parametrize("line, tier", [
    ("Switch the breaker OFF and prove dead with the tester before touching anything.", "gate"),
    ("Assume every wire is live until the tester shows no power.", "gate"),
    ("Keep the breaker off while you press the bare wire ends into the tabs.", "danger"),
    ("Never let the red clamp touch the black clamp or any other metal.", "danger"),
    ("Keep fingers clear of the crimp jaws; they pinch hard.", "caution"),
    ("", ""),
])
def test_safety_tiers(line, tier):
    assert safety.tier(line) == tier


def test_bundled_mains_lessons_gate_and_say_electricity(c):
    meta = c.get("/api/tutorials/light-switch").json()
    assert meta["safety"] == {"level": "gate", "electric": True}
    d = c.get("/api/tutorials/light-switch/details").json()
    tiers = [d[k]["hazard"] for k in sorted(d, key=int)]
    assert "gate" in tiers[:3] and all(t in ("gate", "danger") for t in tiers)  # proven dead before the wires are touched
    assert c.get("/api/tutorials/tie-a-tie").json()["safety"]["level"] == ""


@pytest.mark.parametrize("said, name", [
    ("no, it's right", "override"), ("you're wrong", "override"), ("mine is fine", "override"),
    ("the breaker is off", "safe"), ("it's safe", "safe"), ("is it right", "check_me"),
    ("listo", "safe"), ("तैयार", "safe"), ("nein", "no"), ("sí", "yes"),
])
def test_voice_safety_and_override(said, name):
    assert command(said) == name


def test_translation_round_trip_keeps_ids_lists_and_hazard():
    steps = {"1": {"say": "Put it in.", "placements": ["a → b", "c → d"], "watch_for": ["x"], "check_yourself": "ok",
                   "safety": "Turn the power off.", "hazard": "gate"}}
    flat = i18n.flatten(steps)
    assert flat["1.placements.1"] == "c → d"
    out = i18n.unflatten(steps, {k: v.upper() for k, v in flat.items()})
    assert out["1"]["placements"] == ["A → B", "C → D"] and out["1"]["hazard"] == "gate"
    assert asyncio.run(i18n.translate(flat, "en")) == flat  # English: nothing sent


def test_phrases_and_details_fall_back_to_english_without_a_translation(c, monkeypatch):
    assert c.get("/api/phrases").json()["your_turn"] == "Your turn."
    assert c.get("/api/phrases?lang=xx").json()["your_turn"] == "Your turn."  # unknown language: English


def test_session_language_is_kept_and_unknown_is_english(c):
    sid = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True,
                                        "lang": "es"}).json()["session_id"]
    providers.sessions.sessions.pop(sid, None)  # as after a restart: rebuilt from the log
    assert providers.sessions._load(sid)["lang"] == "es"
    assert providers.languages(providers.sessions._load(sid)) == ["Spanish", "English"]
    sid2 = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True,
                                         "lang": "klingon"}).json()["session_id"]
    assert providers.sessions._load(sid2)["lang"] == "en"


def test_delete_my_data_removes_this_browsers_logs_only(c):
    mine = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True}).json()["session_id"]
    other = TestClient(app)
    theirs = other.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "live", "age_confirmed": True}).json()["session_id"]
    r = c.delete("/api/me")
    assert r.status_code == 200 and r.json()["session_logs"] >= 1
    assert not providers.sessions._path(mine).exists()
    assert providers.sessions._path(theirs).exists()


def test_old_session_logs_are_purged():
    sid = providers.sessions.start("v", "install-thermostat", "replay", None, "sha256:x")
    old = time.time() - (providers.LOG_RETENTION_DAYS + 1) * 86400
    os.utime(providers.sessions._path(sid), (old, old))
    providers.sessions.purge()
    assert not providers.sessions._path(sid).exists()


def test_metrics_and_client_errors(c):
    assert c.post("/api/client-errors", json={"message": "x is undefined", "where": "follow.js:12"}).status_code == 204
    m = c.get("/api/metrics").json()
    assert {"models", "rate_limit", "spend", "client_errors", "funnel"} <= m.keys()
    assert m["client_errors"][-1]["message"] == "x is undefined"
    assert m["rate_limit"]["rpm"] == 60 and "completion_rate" in m["funnel"]


def test_phrases_file_matches_the_lesson_screen_defaults():
    """The lesson screen speaks PHRASES_EN until /api/phrases answers: same keys as config/phrases.json."""
    from pathlib import Path
    js = (Path(__file__).parent.parent / "web/js/ui/follow.js").read_text(encoding="utf-8")
    block = js[js.index("const PHRASES_EN"):js.index("};", js.index("const PHRASES_EN"))]
    for key in json.loads((Path(__file__).parent.parent / "config/phrases.json").read_text()):
        assert f"{key}:" in block, key
