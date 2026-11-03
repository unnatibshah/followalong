"""Regressions for the server hardening pass: cost abuse, log growth, hosts, metrics, plan edits, model-output parsing,
request validation, safety wording, link imports, headers and unknown API routes. No live model calls."""

import asyncio
import io
import json
import runpy

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from server import providers, recordings, safety, spark
from server.config import CAPS, MODELS
from server.gateway import Budget, Gateway, GatewayError, cost
from server.main import CSP, app

from test_api import session
from test_persistence import LIMITS, Clock

pytestmark = pytest.mark.usefixtures("scripted")


def _jpeg() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (64, 36), "grey").save(out, "JPEG")
    return out.getvalue()


JPEG = _jpeg()


@pytest.fixture
def c():
    return TestClient(app)


def post_meta(c, sid, route, meta):
    return c.post(f"/api/sessions/{sid}/{route}", files={"frame": ("f.jpg", JPEG, "image/jpeg")},
                  data={"meta": json.dumps(meta)})


# 1. cost abuse
def test_replay_sessions_never_reach_the_model(c, fake, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    f = fake([])
    sid = session(c, "replay", recording_id="with-mistakes")
    r = post_meta(c, sid, "questions", {"question": "is this right?", "step_id": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "replay_only"
    assert c.post(f"/api/sessions/{sid}/transcribe", content=b"RIFF").json()["error"]["code"] == "replay_only"
    assert f.calls == []


def test_live_sessions_are_limited_per_ip_too(tmp_path):
    v = providers.Visitors(tmp_path / "v.json", {**LIMITS, "ip_sessions_per_day": 1}, True, Clock("2026-11-06T15:00:00+00:00"))
    v.admit_live("cookie-1", "s1", "1.2.3.4")
    with pytest.raises(providers.ApiError, match="live sessions"):
        v.admit_live("cookie-2", "s2", "1.2.3.4")  # a new cookie, the same address
    v.admit_live("cookie-3", "s3", "5.6.7.8")


# 2. session logs
def test_session_log_is_capped_and_an_ended_session_takes_nothing_more(c, monkeypatch):
    monkeypatch.setattr(providers, "LOG_MAX_BYTES", 2000)
    sid = session(c, "replay", recording_id="with-mistakes")
    for _ in range(30):
        assert c.post(f"/api/sessions/{sid}/events", json={"type": "x", "data": {"pad": "a" * 200}}).status_code == 204
    assert providers.sessions._path(sid).stat().st_size < 2000 + 600  # one line past the cap at most
    assert c.post(f"/api/sessions/{sid}/end", json={"reason": "left"}).status_code == 200  # session_end still written
    for route in ("events", "checks", "questions", "watch"):
        r = c.post(f"/api/sessions/{sid}/{route}", json={"type": "x"}) if route == "events" else post_meta(c, sid, route, {})
        assert r.status_code == 409 and r.json()["error"]["code"] == "session_ended", route
    assert c.post(f"/api/sessions/{sid}/end", json={}).status_code == 200  # the beacon after /end


# 3. DNS rebinding
def test_unknown_hosts_are_refused():
    evil = TestClient(app, base_url="http://rebind.example:8000")
    assert evil.get("/api/tutorials").status_code == 400
    assert evil.get("/healthz").status_code == 200  # platform health checks
    assert TestClient(app, base_url="http://127.0.0.1:8000").get("/api/tutorials").status_code == 200


# 4. metrics
def test_hosted_metrics_need_the_token(c, monkeypatch):
    monkeypatch.setattr(providers, "HOSTED", True)
    assert c.get("/api/metrics").status_code == 404  # no token configured
    monkeypatch.setenv("FA_METRICS_TOKEN", "s3cret")
    assert c.get("/api/metrics?token=nope").status_code == 404
    assert c.get("/api/metrics?token=s3cret").status_code == 200
    assert c.get("/api/metrics", headers={"x-metrics-token": "s3cret"}).status_code == 200


# 5. plan edits
@pytest.mark.parametrize("checks", ["oops", [1], None, [{"id": "x", "kind": "judged", "text": "x", "type": "judged"}, "x"],
                                    [{"id": "x", "kind": "measured", "type": "count", "a": "nut", "text": "two"}]])
def test_malformed_plan_edits_are_invalid_plan(c, checks):
    plan = c.get("/api/tutorials/install-thermostat/plan").json()
    plan["steps"][0]["checks"] = checks
    r = c.put("/api/tutorials/install-thermostat/plan", json=plan)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_plan"


# 6. reviewed keeps the hash
def test_marking_reviewed_keeps_the_plan_hash_and_its_replays(c):
    plan = c.get("/api/tutorials/install-thermostat/plan").json()
    saved = c.put("/api/tutorials/install-thermostat/plan", json={**plan, "reviewed": True}).json()
    assert saved["reviewed"] is True and saved["plan_hash"] == plan["plan_hash"]
    assert session(c, "replay", recording_id="with-mistakes")
    plan["steps"][0]["instruction"] += " Carefully."
    assert c.put("/api/tutorials/install-thermostat/plan", json=plan).json()["plan_hash"] != saved["plan_hash"]


# 7. gateway
@pytest.mark.parametrize("answer", [httpx.Response(200, text="<html>hiccup</html>"), httpx.Response(200, json=[])])
def test_a_malformed_200_is_a_gateway_error(tmp_path, monkeypatch, answer):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    g = Gateway(transport=httpx.MockTransport(lambda r: answer), sleep=lambda s: asyncio.sleep(0),
                budget=Budget(tmp_path / "b.json", CAPS, False))
    with pytest.raises(GatewayError):
        asyncio.run(g.responses({"model": MODELS["judged"], "input": []}, 5))


def test_cost_takes_null_usage_fields():
    assert cost(MODELS["judged"], {"input_tokens": None, "output_tokens": 5,
                                   "input_tokens_details": {"cached_tokens": None}}) >= 0


# 8. spark
def test_judge_keeps_the_cost_of_an_off_schema_answer(fake):
    fake([{"results": "ab", "hint": ""}])  # a string where the results list goes: AttributeError in parse
    with pytest.raises(GatewayError) as e:
        asyncio.run(spark.judge({"id": 1, "instruction": "x"}, [{"id": "a", "text": "x"}], JPEG, JPEG, None))
    assert e.value.code == "invalid_output" and e.value.cost_usd == 0.02


# 9. request validation
@pytest.mark.parametrize("route,meta", [("watch", {"step_id": 1, "elapsed_s": "abc"}), ("watch", {"step_id": 1, "elapsed_s": 1e999}),
                                        ("watch", {"step_id": True}), ("questions", {"step_id": True, "question": "hi"}),
                                        ("checks", {"step_id": True, "kind": "full", "check_no": 1, "trigger": "still"})])
def test_watch_and_step_ids_are_validated(c, route, meta):
    sid = session(c, "live")
    assert post_meta(c, sid, route, meta).status_code == 400


# 10. frame time
def test_a_unicode_digit_frame_time_is_not_found(c):
    assert c.get("/api/recordings/with-mistakes/frames/%C2%B2").status_code == 404


# 11. promote
def test_promote_sorts_the_timeline_by_time(tmp_path, monkeypatch):
    for k in ("ROOT", "BUNDLED", "DATA"):
        monkeypatch.setattr(recordings, k, tmp_path / k)
    src = tmp_path / "ROOT" / "rec-x"
    (src / "frames").mkdir(parents=True)
    (src / "meta.json").write_text(json.dumps({"id": "rec-x", "title": "t"}))
    (src / "results.jsonl").write_text("")
    evs = [{"at": "2026-11-01T18:00:01.500000Z", "type": "b"}, {"at": "2026-11-01T18:00:01Z", "type": "a"}]
    (src / "timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evs))
    dst = recordings.promote("rec-x", "x", None, None, False)
    assert [json.loads(x)["type"] for x in (dst / "timeline.jsonl").read_text().splitlines()] == ["a", "b"]


# 12. safety
def test_safety_self_check_and_reworded_gates(capsys):
    runpy.run_path(safety.__file__, run_name="__main__")  # its self-check (run_module warns: already imported)
    assert capsys.readouterr().out.strip() == "ok"
    assert safety.tier("Keep children away. Switch the breaker off and prove it dead with the tester.") == "gate"
    assert safety.tier("Make sure the power is off at the breaker before you touch any wire.") == "gate"


# 13. link imports
def test_link_imports_never_use_the_generic_extractor(tmp_path, monkeypatch):
    import yt_dlp

    from server.tutorials import Rejected, fetch_link
    seen = []

    class Y:
        def __init__(self, opts): seen.append(opts)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): return {"duration": 60, "license": None}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Y)
    with pytest.raises(Rejected):
        fetch_link("https://www.youtube.com/watch?v=VdnkRQF5Cps", 600, tmp_path)
    assert seen[0]["allowed_extractors"] == ["youtube", "vimeo"]


# 14. headers
def test_csp_blocks_plugins_base_and_foreign_forms(c):
    assert {"object-src 'none'", "base-uri 'none'", "form-action 'self'"} <= set(CSP.split("; "))
    assert c.get("/healthz").headers["content-security-policy"] == CSP


# 15. unknown API routes
@pytest.mark.parametrize("method", ["get", "post"])
def test_unknown_api_routes_answer_in_the_api_shape(c, method):
    r = getattr(c, method)("/api/no-such-thing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
