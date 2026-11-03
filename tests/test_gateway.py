"""UT-60-63, 70, 71 (gateway), UT-12 (SAM confidence filter); and one opt-in call to Meta (`pytest -m live`)."""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

import httpx2 as httpx
import pytest

from server import sam
from server.gateway import Budget, Gateway, GatewayError, cost

KEY = "sk-test-not-a-real-key"
SAM_BODY = {"model": "sam-3.1", "input": [{"type": "message", "role": "user", "content": [
    {"type": "input_text", "text": "blue wire"}, {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"}]}]}


def gateway(handler, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    sleeps = []

    async def sleep(s):
        sleeps.append(s)
    budget = Budget(Path(tempfile.mkdtemp()) / "budget.json", {"session": 1.5, "day": 10, "total": 50}, enforce=False)
    return Gateway(transport=httpx.MockTransport(handler), sleep=sleep, budget=budget), sleeps


def call(gw, body=SAM_BODY):
    return asyncio.run(gw.responses(body, 8, "sess-1"))


def test_def007_startup_refuses_a_model_off_the_allow_list():
    from server.config import MODELS, check_models
    check_models(MODELS)
    for bad in ({**MODELS, "judged": "muse-spark-1.3-contributor"}, {**MODELS, "plan": "gpt-4o"}):
        with pytest.raises(SystemExit, match="allow-list"):
            check_models(bad)


def test_def001_one_client_per_event_loop(monkeypatch):
    """A pooled client is bound to its loop: a second asyncio.run() must not reuse the first loop's client."""
    gw, _ = gateway(lambda r: httpx.Response(200, json={"output_text": ""}), monkeypatch)
    call(gw)
    first = gw._client
    call(gw)
    assert gw._client is not first

    async def twice():
        await gw.responses(SAM_BODY, 8)
        c = gw._client
        await gw.responses(SAM_BODY, 8)
        return c is gw._client
    assert asyncio.run(twice())  # still one pooled client within a loop


@pytest.mark.parametrize("model", ["muse-spark-1.3-contributor", "gpt-4o"])
def test_ut60_allow_list_refuses_before_any_network_call(monkeypatch, model):
    seen = []
    gw, _ = gateway(lambda r: seen.append(r) or httpx.Response(200, json={}), monkeypatch)
    with pytest.raises(GatewayError, match="model_not_allowed"):
        call(gw, {**SAM_BODY, "model": model})
    assert seen == []


def test_ut61_store_false_and_cost_counted(monkeypatch):
    seen = []

    def ok(r):
        seen.append(json.loads(r.content))
        return httpx.Response(200, json={"output_text": "", "usage": {"images_processed": 1}})
    gw, _ = gateway(ok, monkeypatch)
    _, usd = call(gw)
    assert seen[0]["store"] is False and usd == 0.0025
    b = gw.budget.snapshot()
    assert b["sessions"]["sess-1"] == 0.0025 and b["total_usd"] == 0.0025 and b["day_usd"] == 0.0025


def test_ut62_63_cost_from_usage():
    usage = {"input_tokens": 3000, "input_tokens_details": {"cached_tokens": 1000},
             "output_tokens": 300, "output_tokens_details": {"reasoning_tokens": 100}}
    assert cost("muse-spark-1.3", usage) == pytest.approx(0.003925)
    assert sum(cost("sam-3.1", {"images_processed": 1}) for _ in range(4)) == pytest.approx(0.01)


@pytest.mark.parametrize("responses,calls,waits", [
    ([httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={})], 2, [2.0]),
    ([httpx.Response(500), httpx.Response(200, json={})], 2, "jitter"),
    (["timeout", httpx.Response(200, json={})], 2, "jitter"),
    ([httpx.Response(503, headers={"Retry-After": "60"})], 1, []),  # longer than the 8 s timeout: fallback now
    *[([httpx.Response(s)], 1, []) for s in (400, 401, 402, 403, 404, 413)],
])
def test_ut70_retry_rules(monkeypatch, responses, calls, waits):
    seen = []

    def handler(r):
        seen.append(r)
        nxt = responses[len(seen) - 1]
        if nxt == "timeout":
            raise httpx.ReadTimeout("slow", request=r)
        return nxt
    gw, sleeps = gateway(handler, monkeypatch)
    try:
        call(gw)
    except GatewayError:
        pass
    assert len(seen) == calls
    if waits == "jitter":
        assert len(sleeps) == 1 and 0.5 <= sleeps[0] <= 1.0
    else:
        assert sleeps == waits


def test_ut70_fails_after_one_retry(monkeypatch):
    gw, _ = gateway(lambda r: httpx.Response(502), monkeypatch)
    with pytest.raises(GatewayError):
        call(gw)


def test_ut71_logs_hold_model_latency_cost_only(monkeypatch, caplog):
    gw, _ = gateway(lambda r: httpx.Response(200, json={"output_text": "secret words", "usage": {}}), monkeypatch)
    with caplog.at_level(logging.INFO, logger="followalong.gateway"):
        call(gw)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "followalong.gateway"]
    assert len(lines) == 1 and set(lines[0]) == {"event", "model", "latency_ms", "cost_usd"}
    assert not any(s in caplog.text for s in (KEY, "QUJD", "blue wire", "secret words", "Authorization"))


def test_ut12_sam_confidence_filter(monkeypatch):
    async def responses(body, timeout_s, session_id=None):
        return {"output_text": "<0f>" + ",".join(rec(i, c) for i, c in ((0, 0.3), (1, 0.8)))}, 0.0025
    monkeypatch.setattr(sam.gateway, "responses", responses)
    res = asyncio.run(sam.segment(b"", "blue wire"))
    assert [i["score"] for i in res["instances"]] == [0.8]


def rec(obj_id, conf):
    # A 4x2 all-ones one_bit mask, built with Meta's parser's own encoder so the payload is canonical.
    from meta_sam_parser._mask_codec import _encode_raster
    payload = _encode_raster(bytes([1] * 8), 4, 2)
    return (f"{obj_id}<|box;x1=10;y1=10;x2=13;y2=11;w=1280;h=720;c={conf}|>"
            f"<|mask;x=0;y=0;c={conf};data=2,4,{payload}|>")


@pytest.mark.live
def test_live_sam_finds_the_six_terminals():
    """Real API, ~$0.005: `pytest -m live`. Needs MODEL_API_KEY in the environment."""
    frame = (Path(__file__).parent.parent / "data/tutorials/install-thermostat/frames/step-7.jpg").read_bytes()
    res = asyncio.run(sam.segment(frame, "circle"))
    assert len(res["instances"]) == 6 and res["size"] == (1280, 720)
