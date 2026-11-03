"""Honest check latency: CF-Bench frames one at a time, no pacing (batch runs queue behind the 60 requests/min limit),
with a pause between frames so the limit is never hit. Verifier (hand gate off) and product (hand gate on).

    cd app && set -a; . ./.env; set +a; .venv/bin/python tools/latency.py [n]   # → eval/cfbench/latency.json
"""

import asyncio
import json
import random
import statistics
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from server import evaluate  # noqa: E402
from server.checks import engine  # noqa: E402
from server.providers import end_box  # noqa: E402


async def main(n: int) -> None:
    labels = evaluate.load_labels(APP / "eval" / "cfbench" / "labels.jsonl")
    random.Random(7).shuffle(labels)
    cache, out = {}, {"verify": [], "soft": []}
    for i, lab in enumerate(labels[:n]):
        mode = "verify" if i % 2 == 0 else "soft"
        step = evaluate.ablate(evaluate.step_of(lab, cache), mode)
        end = APP / "data" / "tutorials" / lab["tutorial_id"] / "frames" / f"step-{lab['step_id']}.jpg"
        res = await engine.run(step, (APP / lab["frame"]).read_bytes(), "full", end_frame=end.read_bytes(),
                               end_box=end_box(end, step) if step.get("objects") else None)
        if not res["fallback"]:
            out[mode].append({"ms": res["latency_ms"], "stages": res["stages_ms"], "verdict": res["verdict"]})
        print(mode, res["verdict"], res["latency_ms"], res["stages_ms"], flush=True)
        await asyncio.sleep(6)
    summary = {m: {"n": len(v), "p50_s": round(statistics.median(x["ms"] for x in v) / 1000, 2),
                   "p90_s": round(sorted(x["ms"] for x in v)[int(0.9 * (len(v) - 1))] / 1000, 2)} for m, v in out.items() if v}
    (APP / "eval" / "cfbench" / "latency.json").write_text(json.dumps({"summary": summary, "rows": out}, indent=1) + "\n")
    print(summary)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 24))
