"""Human verification (#/annotate, local only): a person checks what the automatic evaluation assumed.

    steps   per tutorial step: boundaries, instruction, exact directions, safety line → eval/human/steps.jsonl
    real    per frame of a real "common mistakes" video (tools/real_mistakes.py): which step it shows and whether it's
            correct, in progress, a mistake (which one) or not relevant → eval/human/real.jsonl

Answers are appended (the newest per key wins), so annotating twice just corrects the first answer. Never served
when FA_HOSTED=true: it reads and writes the repo.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from .api import _json_body
from .config import APP, DATA, HOSTED
from .providers import ApiError, tutorials
from .store import read_json

router = APIRouter()
CF = APP / "eval" / "cfbench"
HUMAN = APP / "eval" / "human"
KINDS = ("steps", "real")
REAL = APP / "eval" / "realmistakes"


def _local() -> None:
    if HOSTED:
        raise ApiError("not_found", "Annotation runs only on a local FollowAlong.")


def _jsonl(path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def answers(kind: str) -> dict:
    """{key: the newest answer} for one kind."""
    return {a["key"]: a for a in _jsonl(HUMAN / f"{kind}.jsonl")}


@router.get("/api/annotate/tasks")
def tasks():
    _local()
    listing = {t["id"]: t for t in tutorials.list("annotator") if t["source"] == "bundled"}
    steps = []
    for tid, t in listing.items():
        plan = tutorials.plan(tid, "annotator")
        det = read_json(DATA / "tutorials" / tid / "details.json", {}).get("steps", {})
        steps.append({"tutorial_id": tid, "title": t["title"], "steps": [
            {"key": f"{tid}|{s['id']}", "id": s["id"], "start": s["start"], "end": s["end"], "instruction": s["instruction"],
             "done_looks_like": s.get("done_looks_like", ""), **{k: (det.get(str(s["id"])) or {}).get(k, "" if k in ("say", "safety", "check_yourself") else [])
                                                                 for k in ("say", "placements", "safety", "watch_for", "check_yourself")}}
            for s in plan["steps"]]})
    real = []
    for row in _jsonl(REAL / "index.jsonl"):
        if row["tutorial_id"] in listing:
            plan = tutorials.plan(row["tutorial_id"], "annotator")
            real.append({**row, "frame": row["key"], "steps": [{"id": s["id"], "instruction": s["instruction"],
                                                                 "mistakes": s.get("common_mistakes") or []} for s in plan["steps"]]})
    props = {"real": {}, "steps": {}}  # the labelling agents' proposals (eval/human/proposals/), for approve / deny
    for f in sorted((HUMAN / "proposals").glob("*.jsonl")):
        for row in _jsonl(f):
            props["steps" if f.name.startswith("steps-") else "real"][row["key"]] = row
    rank = {"low": 0, "medium": 1, "high": 2}
    real.sort(key=lambda r: (rank.get((props["real"].get(r["key"]) or {}).get("confidence"), 3), r["video"], r["t"]))
    return {"steps": steps, "real": real, "proposals": props, "done": {k: list(answers(k)) for k in KINDS}}



@router.get("/api/annotate/image")
def image(path: str):
    """A CF-Bench photo (only under eval/cfbench/frames/)."""
    _local()
    p = (APP / path).resolve()
    inside = any(str(p).startswith(str((d / "frames").resolve()) + "/") for d in (CF, REAL))
    if not inside or p.suffix != ".jpg" or not p.exists():
        raise ApiError("not_found")
    return FileResponse(p, media_type="image/jpeg")


@router.post("/api/annotate")
async def save(request: Request):
    _local()
    body = await _json_body(request)  # non-JSON or not an object: 400
    if body.get("kind") not in KINDS or not isinstance(body.get("key"), str) or len(json.dumps(body)) > 20000:
        raise ApiError("bad_request", "Send kind (steps, frames or coach), key and the answer.")
    HUMAN.mkdir(parents=True, exist_ok=True)
    row = {**body, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with (HUMAN / f"{body['kind']}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"saved": True, "count": len(answers(body["kind"]))}
