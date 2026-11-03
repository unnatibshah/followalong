"""Exact, spoken step directions (where each piece goes: breadboard row and column, terminal label, polarity, colour
order). Muse Spark watches the video once more, with the narration and each step's finished frame, and writes them
for the plan's existing steps. Kept next to the tutorial (`details.json`), outside the plan, so checks, plan hashes
and recorded replays don't change.

    cd app && set -a; . ./.env; set +a
    .venv/bin/python -m server.details [tutorial_id ...]      # bundled tutorials → data/tutorials/<id>/details.json
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

from . import sam
from .config import DATA, MODELS, TIMEOUTS, UPLOAD, _prompt
from .gateway import gateway

PROMPT = _prompt("step_details")
PLANS = Path(__file__).parent / "fixtures" / "plans"
_STR = {"type": "string"}
DETAILS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["actor", "steps"],
    "properties": {"actor": _STR, "steps": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "say", "placements", "watch_for", "check_yourself", "safety"],
        "properties": {"id": {"type": "integer"}, "say": _STR, "placements": {"type": "array", "items": _STR},
                       "watch_for": {"type": "array", "items": _STR}, "check_yourself": _STR, "safety": _STR}}}}}


def request(plan: dict, file_id: str, duration: float, frames: dict[int, bytes], transcript: str | None) -> dict:
    steps = "\n".join(f"{s['id']}. [{s['start']:.0f}–{s['end']:.0f} s] {s['instruction']} (done: {s['done_looks_like']})"
                      for s in plan["steps"])
    content = [{"type": "input_text", "text": PROMPT["user"].replace("{duration}", f"{duration:.0f}").replace("{steps}", steps)},
               {"type": "input_file", "file_id": file_id}]
    if transcript:
        content.append({"type": "input_text", "text": "Narration (seconds, then words):\n" + transcript})
    for sid, jpg in frames.items():
        content += [{"type": "input_text", "text": f"Step {sid} finished:"},
                    {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}]
    return {"model": MODELS["plan"], "instructions": PROMPT["system"],
            "input": [{"type": "message", "role": "user", "content": content}],
            "max_output_tokens": MODELS["plan_max_output_tokens"],
            "text": {"format": {"type": "json_schema", "name": "step_details", "schema": DETAILS_SCHEMA, "strict": True}}}


def clean(answer: dict, plan: dict) -> dict:
    """{step id (str): details}; only the plan's steps, trimmed lists. Pure."""
    ids = {s["id"] for s in plan["steps"]}
    out = {}
    for s in answer.get("steps") or []:
        if s.get("id") in ids and isinstance(s.get("say"), str) and s["say"].strip():
            out[str(s["id"])] = {"say": s["say"].strip(), "placements": [p.strip() for p in s.get("placements") or [] if p.strip()][:12],
                                 "watch_for": [w.strip() for w in s.get("watch_for") or [] if w.strip()][:2],
                                 "check_yourself": (s.get("check_yourself") or "").strip(),
                                 "safety": (s.get("safety") or "").strip()[:240]}
    return out


async def build(plan: dict, folder: Path, duration: float, transcript: str | None = None,
                file_id: str | None = None) -> dict:
    """Muse Spark → details for `plan`'s steps (video at folder/video.mp4, finished frames in folder/frames/).
    `file_id`: the video already uploaded for the step plan (reused, and the caller deletes it)."""
    frames = {s["id"]: (folder / "frames" / f"step-{s['id']}.jpg").read_bytes() for s in plan["steps"]
              if (folder / "frames" / f"step-{s['id']}.jpg").exists()}
    own = file_id is None
    if own:
        file_id = await gateway.upload_file(folder / "video.mp4", int(UPLOAD["ttl_h"] * 3600))
    try:
        data, usd = await gateway.responses(request(plan, file_id, duration, frames, transcript), TIMEOUTS["plan_s"])
    finally:
        if own:
            await gateway.delete_file(file_id)
    text = sam.output_text(data).strip()
    text = text[text.find("{"): text.rfind("}") + 1]
    answer = json.loads(text)
    actor = " ".join(str(answer.get("actor") or "hand").split())[:40] or "hand"
    return {"prompt_version": PROMPT["version"], "model": MODELS["plan"], "cost_usd": usd, "actor": actor,
            "steps": clean(answer, plan)}


async def main(ids: list[str]) -> None:
    from .tutorials import narration, probe
    for f in sorted(PLANS.glob("*.json")):
        plan = json.loads(f.read_text(encoding="utf-8"))
        tid = plan["tutorial_id"]
        if ids and tid not in ids:
            continue
        folder = DATA / "tutorials" / tid
        info = await probe(folder / "video.mp4")
        text = await narration(folder / "video.mp4", {}) if info["audio"] else None
        det = await build(plan, folder, info["duration"], text)
        (folder / "details.json").write_text(json.dumps(det, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{tid}: {len(det['steps'])}/{len(plan['steps'])} steps, ${det['cost_usd']:.3f}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
