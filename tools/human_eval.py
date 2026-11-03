"""The feedback loop: FollowAlong against human-approved labels of REAL mistakes (eval/human/real.jsonl, from #/annotate).

Ground truth = the approved or corrected answers (a denied proposal without a correction is dropped). Every frame
that shows a tutorial step goes through the live coach (one look) and the grounded check (soft gate), with real
Meta calls; answers are cached in eval/human/model-answers.json so a re-score (after a prompt or threshold change,
delete the cache entries you want re-asked) is free. Writes eval/human/report.json and prints the misses.

    cd app && set -a; . ./.env; set +a; .venv/bin/python tools/human_eval.py [--tag NAME]
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from server import evaluate, providers, voice  # noqa: E402
from server.checks import engine  # noqa: E402
from server.providers import end_box  # noqa: E402

HUMAN = APP / "eval" / "human"
CACHE = HUMAN / "model-answers.json"


def truth() -> list[dict]:
    rows = {}
    for line in (HUMAN / "real.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["key"]] = r  # the newest decision per frame wins
    return [r for r in rows.values() if r.get("decision") != "denied" and r.get("state") in ("correct", "in_progress", "mistake")
            and r.get("step_id")]


async def ask(lab: dict, tag: str, cache: dict, plans: dict) -> dict:
    key = f"{lab['key']}|{lab['step_id']}|{tag}|{voice.WATCH_PROMPT['version']}"
    if key in cache:
        return cache[key]
    tid = lab["tutorial_id"]
    plan = plans.setdefault(tid, evaluate.plan_for(tid, {}))
    step = next(s for s in plan["steps"] if s["id"] == lab["step_id"])
    frame = (APP / lab["key"]).read_bytes()
    end = APP / "data" / "tutorials" / tid / "frames" / f"step-{step['id']}.jpg"
    ctx = {**plan, "title": tid, "actor": providers.tutorials.actor(tid, "x")}
    await evaluate._paced()
    look = await voice.watch(ctx, providers.tutorials.details(tid, "x"), step,
                             {"recent": [], "said": [], "elapsed_s": 20, "encourage": False}, frame, end.read_bytes(), None)
    for _ in range(4):
        await evaluate._paced()
    chk = await engine.run({**step, "actor": providers.tutorials.actor(tid, "x")}, frame, "full", end_frame=end.read_bytes(),
                           end_box=end_box(end, step) if step.get("objects") else None)
    cache[key] = {"watch": {k: look[k] for k in ("status", "say", "observation")},
                  "check": {"verdict": chk["verdict"], "hint": chk["hint"], "fallback": chk["fallback"]}}
    CACHE.write_text(json.dumps(cache, indent=1) + "\n", encoding="utf-8")
    return cache[key]


async def main(tag: str) -> None:
    labels = truth()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    plans, rows = {}, []
    gate = asyncio.Semaphore(3)

    async def one(lab):
        async with gate:
            a = await ask(lab, tag, cache, plans)
        rows.append({**lab, **a})
    await asyncio.gather(*(one(lab) for lab in labels))
    by = {s: [r for r in rows if r["state"] == s] for s in ("correct", "in_progress", "mistake")}
    pct = lambda k, n: f"{k}/{n} ({round(100 * k / n)}%)" if n else "–"  # noqa: E731
    report = {
        "frames": len(rows), "per_state": {s: len(v) for s, v in by.items()},
        "coach_flags_mistakes": pct(sum(r["watch"]["status"] in ("mistake", "cant_see") for r in by["mistake"]), len(by["mistake"])),
        "coach_says_mistake_on_mistakes": pct(sum(r["watch"]["status"] == "mistake" for r in by["mistake"]), len(by["mistake"])),
        "check_false_done_on_mistakes": pct(sum(r["check"]["verdict"] == "done" for r in by["mistake"]), len(by["mistake"])),
        "coach_sees_done_on_correct": pct(sum(r["watch"]["status"] == "looks_done" for r in by["correct"]), len(by["correct"])),
        "check_done_on_correct": pct(sum(r["check"]["verdict"] == "done" for r in by["correct"]), len(by["correct"])),
        "coach_false_done_in_progress": pct(sum(r["watch"]["status"] == "looks_done" for r in by["in_progress"]), len(by["in_progress"])),
        "watch_status_by_truth": {s: dict(Counter(r["watch"]["status"] for r in v)) for s, v in by.items()},
        "check_verdict_by_truth": {s: dict(Counter(r["check"]["verdict"] for r in v)) for s, v in by.items()},
        "missed_mistakes": [{"frame": r["key"], "step": r["step_id"], "human": r["mistake"], "coach": r["watch"],
                             "check": r["check"]} for r in by["mistake"] if r["watch"]["status"] not in ("mistake", "cant_see")],
    }
    (HUMAN / f"report-{tag}.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "missed_mistakes"}, indent=1))
    print(f"\n{len(report['missed_mistakes'])} mistakes the coach missed:")
    for m in report["missed_mistakes"]:
        print(f"- step {m['step']}: {m['human']}  | coach {m['coach']['status']}: {m['coach']['observation'][:90]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v1", help="name of this run (e.g. v1 before a change, v2 after)")
    asyncio.run(main(ap.parse_args().tag))
