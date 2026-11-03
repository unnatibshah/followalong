"""Reference self-calibration: a plan's measured checks are run on the tutorial's own end frames before anyone uses
them. A check that fails on the frame that *defines* "done" is wrong by construction (e.g. Muse Spark wrote
`inside(red wire, breadboard)`, but a plugged-in wire is mostly outside the board), so it is disabled and marked
`needs_review`; an object SAM 3.1 can't find on its own end frame is dropped with the checks that name it. A step left
with no enabled check gets its "done looks like" as a judged check. Only disables, never tunes a threshold.

    cd app && set -a; . ./.env; set +a
    .venv/bin/python -m server.calibrate [tutorial_id ...]     # bundled plans in place; notes → eval/cfbench/calibration.json
"""

import asyncio
import json
import sys
from pathlib import Path

from . import sam
from .checks import engine
from .config import APP, DATA
from .tutorials import rehash

PLANS = Path(__file__).parent / "fixtures" / "plans"
NOTES = APP / "eval" / "cfbench" / "calibration.json"


def goal_check(step: dict) -> dict:
    return {"kind": "judged", "type": "judged", "a": None, "b": None, "n": None, "side": None,
            "text": step.get("done_looks_like") or step["instruction"], "confidence": 0.9, "needs_review": True,
            "enabled": True, "id": f"s{step['id']}goal"}


def apply(step: dict, res: dict) -> dict:
    """Pure: the step after calibration on `res` (an engine result for its own end frame, measured only)."""
    missing = {o["name"] for o in res["objects"] if not o["found"]}
    failed = {r["check_id"]: r.get("value") for r in res["results"] if r["kind"] == "measured" and not r["pass"]}
    checks = []
    for c in step["checks"]:
        bad = c["kind"] == "measured" and c.get("enabled", True) and (c["id"] in failed or {c.get("a"), c.get("b")} & missing)
        checks.append({**c, "enabled": False, "needs_review": True} if bad else c)
    step = {**step, "checks": checks, "objects": [o for o in step.get("objects") or [] if o["name"] not in missing]}
    if not any(c.get("enabled", True) for c in checks):
        step["checks"] = checks + [goal_check(step)]
    return step


async def calibrate(plan: dict, frames: Path) -> tuple[dict, list[dict]]:
    notes, steps = [], []
    for st in plan["steps"]:
        end = frames / f"step-{st['id']}.jpg"
        if not st.get("objects") or not end.exists():
            steps.append(st)
            continue
        res = await engine.run({**st, "actor": "none"}, end.read_bytes(), "recheck")  # measured only, no actor
        if res["fallback"]:
            steps.append(st)  # SAM failed: nothing learned, keep the step as it was
            notes.append({"step": st["id"], "skipped": res["fallback"]})
            continue
        new = apply(st, res)
        off = [c["id"] for c, n in zip(st["checks"], new["checks"]) if c.get("enabled", True) and not n.get("enabled", True)]
        notes.append({"step": st["id"], "disabled": off,
                      "values": {r["check_id"]: r.get("value") for r in res["results"] if r["kind"] == "measured"},
                      "objects_dropped": [o["name"] for o in st["objects"] if o not in new["objects"]],
                      "goal_added": len(new["checks"]) > len(st["checks"])})
        steps.append(new)
    return rehash({**plan, "steps": steps}), notes


async def main(ids: list[str]) -> None:
    from .evaluate import _paced  # the API allows 60 requests a minute
    real = sam.segment

    async def paced(*a, **kw):
        await _paced()
        return await real(*a, **kw)
    sam.segment = paced
    allnotes = json.loads(NOTES.read_text(encoding="utf-8")) if NOTES.exists() else {}
    for f in sorted(PLANS.glob("*.json")):
        plan = json.loads(f.read_text(encoding="utf-8"))
        if ids and plan["tutorial_id"] not in ids:
            continue
        new, notes = await calibrate(plan, DATA / "tutorials" / plan["tutorial_id"] / "frames")
        f.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        allnotes[plan["tutorial_id"]] = notes
        off = sum(len(n.get("disabled", [])) for n in notes)
        print(f"{plan['tutorial_id']}: {off} checks disabled, "
              f"{sum(len(n.get('objects_dropped', [])) for n in notes)} objects dropped", flush=True)
    NOTES.write_text(json.dumps(allnotes, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
