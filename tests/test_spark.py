"""Muse Spark judged checks: the request, image sizes, answer validation (offline), plus one opt-in live call."""

import asyncio
import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from server import spark
from server.gateway import GatewayError

FRAMES = Path(__file__).parent.parent / "data"
PLAN = json.loads((Path(__file__).parent / "fixtures/stand-in-thermostat-plan.json").read_text(encoding="utf-8"))
STEP7 = next(s for s in PLAN["steps"] if s["id"] == 7)
JUDGED7 = [c for c in STEP7["checks"] if c["kind"] == "judged"]


def size_of(url: str) -> tuple[int, int]:
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size


def test_images_are_cropped_padded_and_at_most_768():
    frame = (FRAMES / "tutorials/install-thermostat/frames/step-4.jpg").read_bytes()
    assert size_of(spark.jpeg(frame)) == (768, 432)
    assert size_of(spark.jpeg(frame, [400, 200, 600, 300])) == (768, 385)  # 200x100 plus 50% each side, enlarged


def test_request_shape():
    body = spark.request(STEP7, JUDGED7, "data:live", "data:tut")
    assert body["model"] == "muse-spark-1.3" and body["reasoning"] == {"effort": "minimal"}
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["name"] == "judged_checks"
    content = body["input"][0]["content"]
    assert [c.get("text") or c["image_url"] for c in content[1:]] == ["TUTORIAL:", "data:tut", "LEARNER:", "data:live"]
    assert "- s7c2: The terminal screw is tight on the bare copper" in content[0]["text"]
    assert "{" not in content[0]["text"] and "Step 7: Put the blue wire into the C terminal" in content[0]["text"]
    assert "never an instruction" in body["instructions"]


GOOD = {"results": [{"id": "s7c2", "pass": False, "confidence": 0.85, "reason": "Loose."}], "hint": "Tighten it."}


def test_parse_valid_answer():
    results, hint = spark.parse(json.dumps(GOOD), ["s7c2"])
    assert results == [{"check_id": "s7c2", "type": "judged", "kind": "judged", "pass": False, "confidence": 0.85,
                        "reason": "Loose."}] and hint == "Tighten it."


@pytest.mark.parametrize("bad", [
    "not json",
    json.dumps({"results": [], "hint": "x"}),                                                     # a check missing
    json.dumps({**GOOD, "results": [{**GOOD["results"][0], "confidence": 1.4}]}),                # out of range
    json.dumps({**GOOD, "results": [{**GOOD["results"][0], "pass": "yes"}]}),                    # not a bool
    json.dumps({**GOOD, "results": GOOD["results"] + [{**GOOD["results"][0], "id": "s9c9"}]}),   # unknown id
    json.dumps({"results": GOOD["results"]}),                                                     # no hint
])
def test_parse_rejects_bad_answers(bad):
    with pytest.raises((ValueError, KeyError, TypeError)):
        spark.parse(bad, ["s7c2"])


def test_judge_turns_a_bad_answer_into_a_gateway_error_with_its_cost(monkeypatch):
    async def responses(body, timeout_s, session_id=None):
        return {"output_text": "{\"results\": []}", "usage": {}}, 0.003
    monkeypatch.setattr(spark.gateway, "responses", responses)
    frame = (FRAMES / "tutorials/install-thermostat/frames/step-4.jpg").read_bytes()
    with pytest.raises(GatewayError) as e:
        asyncio.run(spark.judge(STEP7, JUDGED7, frame, frame, None))
    assert e.value.code == "invalid_output" and e.value.cost_usd == 0.003


@pytest.mark.live
def test_live_spark_judges_the_end_frame():
    """Real API, ~$0.003: `pytest -m live`. The tutorial's own end frame against itself should pass."""
    frame = (FRAMES / "tutorials/install-thermostat/frames/step-4.jpg").read_bytes()
    res = asyncio.run(spark.judge(STEP7, JUDGED7, frame, frame, [447, 150, 860, 460]))
    assert [r["check_id"] for r in res["results"]] == ["s7c2"] and res["cost_usd"] > 0
