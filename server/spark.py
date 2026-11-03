"""Muse Spark judged checks (TD §4.3, prompt spec P2): one request per step carrying all of its judged checks.

Two pictures: the tutorial's end-of-step frame (TUTORIAL) and a crop of the live frame around the step's objects
(LEARNER), both at most `crop_max_px` on the long side. Structured output (`text.format` json_schema, documented to
work with image input); the answer is validated again here, and anything off raises GatewayError("invalid_output").
"""

import asyncio
import base64
import io
import json
import time

from PIL import Image

from .config import CHECKS, JUDGED_PROMPT, JUDGED_SCHEMA, MODELS, TIMEOUTS
from .gateway import GatewayError, gateway
from .sam import output_text

J = CHECKS["judged"]


def jpeg(data: bytes, box: list[float] | None = None) -> str:
    """A data URL: `data` cropped to `box` (x1, y1, x2, y2 in its own pixels) widened by `crop_pad` and scaled to
    `crop_max_px` on the long side (up too: a wire tip at a printed label is a few pixels in a small crop), or the whole
    frame shrunk to fit."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if box:
        x1, y1, x2, y2 = box
        px, py = (x2 - x1) * J["crop_pad"], (y2 - y1) * J["crop_pad"]
        img = img.crop((max(0, int(x1 - px)), max(0, int(y1 - py)),
                        min(img.width, int(x2 + px) + 1), min(img.height, int(y2 + py) + 1)))
        k = J["crop_max_px"] / max(img.size)
        img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    img.thumbnail((J["crop_max_px"], J["crop_max_px"]))  # keeps the aspect ratio; never enlarges
    out = io.BytesIO()
    img.save(out, "JPEG", quality=J["jpeg_quality"])
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


# A manual's step (CR-006) is judged against the manual's own drawing, not a photo. Said through the step's text so the
# shared prompts (and the replays pinned to their versions) stay as they are.
DRAWING = (" (The TUTORIAL picture is a black-and-white drawing from the instruction manual, not a photo: map each "
           "piece by its shape and how it fits; ignore colour, shading, line style, exploded views and the drawing's angle.)")


def done_text(step: dict, text: str) -> str:
    return text + DRAWING if step.get("manual_page") else text


def request(step: dict, checks: list[dict], live_url: str, tutorial_url: str) -> dict:
    fill = {"id": step.get("id", ""), "instruction": step.get("instruction", ""),
            "done_looks_like": done_text(step, step.get("done_looks_like", "")),
            "common_mistakes": "; ".join(step.get("common_mistakes") or []) or "none listed",
            "checks": "\n".join(f"- {c['id']}: {c['text']}" for c in checks)}
    text = JUDGED_PROMPT["user"]
    for k, v in fill.items():
        text = text.replace("{" + k + "}", str(v))
    return {"model": MODELS["judged"], "instructions": JUDGED_PROMPT["system"],
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": text},
                {"type": "input_text", "text": "TUTORIAL:"}, {"type": "input_image", "image_url": tutorial_url},
                {"type": "input_text", "text": "LEARNER:"}, {"type": "input_image", "image_url": live_url}]}],
            "reasoning": {"effort": MODELS["judged_effort"]},
            "max_output_tokens": MODELS["judged_max_output_tokens"],
            "text": {"format": {"type": "json_schema", "name": "judged_checks", "schema": JUDGED_SCHEMA, "strict": True}}}


def parse(text: str, ids: list[str]) -> tuple[list[dict], str]:
    """Validated per-check results in `ids` order, and the hint. Raises ValueError on anything off-schema."""
    data = json.loads(text)
    by_id = {}
    for r in data["results"]:
        ok = (isinstance(r.get("pass"), bool) and isinstance(r.get("confidence"), (int, float))
              and not isinstance(r["confidence"], bool) and 0 <= r["confidence"] <= 1 and isinstance(r.get("reason"), str))
        if not ok or r.get("id") not in ids or r["id"] in by_id:
            raise ValueError(f"bad judged result {r!r}")
        by_id[r["id"]] = r
    if set(by_id) != set(ids) or not isinstance(data.get("hint"), str):
        raise ValueError("judged answer is missing checks or the hint")
    results = [{"check_id": i, "type": "judged", "kind": "judged", "pass": by_id[i]["pass"],
                "confidence": round(float(by_id[i]["confidence"]), 3), "reason": by_id[i]["reason"].strip()}
               for i in ids]
    return results, data["hint"].strip()


async def judge(step: dict, checks: list[dict], frame: bytes, end_frame: bytes, box: list[float] | None,
                session_id: str | None = None, end_box: list[float] | None = None) -> dict:
    """{"results", "hint", "latency_ms", "cost_usd", "reasoning_tokens"}. Raises GatewayError; an answer that doesn't
    validate raises GatewayError("invalid_output") with its cost on `.cost_usd`."""
    t0 = time.monotonic()
    live_url, tutorial_url = await asyncio.gather(asyncio.to_thread(jpeg, frame, box),
                                             asyncio.to_thread(jpeg, end_frame, end_box if box else None))
    data, usd = await gateway.responses(request(step, checks, live_url, tutorial_url), TIMEOUTS["judged_s"], session_id)
    try:
        results, hint = parse(output_text(data), [c["id"] for c in checks])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        err = GatewayError("invalid_output")
        err.cost_usd = usd
        raise err from e
    usage = data.get("usage") or {}
    return {"results": results, "hint": hint, "latency_ms": round((time.monotonic() - t0) * 1000), "cost_usd": usd,
            "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens")}
