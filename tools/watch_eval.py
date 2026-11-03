"""How the live watcher judges CF-Bench frames (one look each, no memory): does it say `looks_done` on finished
frames, and flag (`mistake` / `cant_see`) the unfinished, missing-part and wrong-colour ones?

    cd app && set -a; . ./.env; set +a
    .venv/bin/python tools/watch_eval.py eval/cfbench/labels-candidates.jsonl    # → eval/cfbench/watch-<file>.json
"""

import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from server import evaluate, providers, voice  # noqa: E402


async def main(path: Path) -> None:
    labels = evaluate.load_labels(path)
    cache, rows = {}, []
    gate = asyncio.Semaphore(4)

    async def one(lab):
        tid = lab["tutorial_id"]
        plan = {**evaluate.plan_for(tid, cache), "title": tid, "actor": providers.tutorials.actor(tid, "x")}
        step = evaluate.step_of(lab, cache)
        end = APP / "data" / "tutorials" / tid / "frames" / f"step-{lab['step_id']}.jpg"
        async with gate:
            await evaluate._paced()
            try:
                r = await voice.watch(plan, providers.tutorials.details(tid, "x"), step,
                                      {"recent": [], "said": [], "elapsed_s": 20, "encourage": False},
                                      (APP / lab["frame"]).read_bytes(), end.read_bytes(), None)
            except Exception as e:  # noqa: BLE001
                r = {"status": "error", "say": str(e), "observation": ""}
        rows.append({**lab, "status": r["status"], "say": r["say"], "observation": r["observation"]})
        print(tid, lab["step_id"], lab["case"], "->", r["status"], "|", r["say"][:90], flush=True)
    await asyncio.gather(*(one(lab) for lab in labels))
    by = defaultdict(lambda: Counter())
    for r in rows:
        by[r["tutorial_id"]][(r["truth"] == "done", r["status"])] += 1
    summary = {}
    for tid, c in sorted(by.items()):
        pos = sum(v for (d, _), v in c.items() if d)
        neg = sum(v for (d, _), v in c.items() if not d)
        summary[tid] = {"done_seen": f"{c[(True, 'looks_done')]}/{pos}",
                        "mistakes_flagged": f"{c[(False, 'mistake')] + c[(False, 'cant_see')]}/{neg}",
                        "false_done": f"{c[(False, 'looks_done')]}/{neg}",
                        "score": round((c[(True, 'looks_done')] / pos + (c[(False, 'mistake')] + c[(False, 'cant_see')]) / neg) / 2, 3)}
    out = APP / "eval" / "cfbench" / f"watch-{path.stem}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
