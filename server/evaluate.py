"""The evaluation command (FR-141, AI evaluation plan §3): run the check engine on labelled frames and write an
EvaluationReport (data dictionary) to eval/reports/<date>.json, which S13 shows (FR-143).

    cd app
    .venv/bin/python -m server.evaluate --labels eval/labels.jsonl                # free: re-scored from recorded answers
    set -a; . ./.env; set +a
    .venv/bin/python -m server.evaluate --labels eval/labels.jsonl --live --note "stand-in frames, not the golden set"
    .venv/bin/python -m server.evaluate --labels eval/labels.jsonl --dry-run     # free: what would run, estimated cost
    .venv/bin/python tools/day1_frames.py <folder>     # day-1 alias: labels from file names, prints only

`--live` calls Meta and keeps every SAM 3.1 answer (masks included) and Muse Spark answer per frame in
eval/recorded.jsonl. Without it, the engine re-runs on those recorded answers with the current config/checks.toml (no
network, $0), so thresholds and tolerances can be re-tuned for free. A change to a prompt or a model needs `--live`;
so does one that makes a check ask for something the live run didn't (it then shows as sam_failed / judge_failed).

Labels: GoldenLabel lines (`frame` relative to app/ or $FA_DATA_DIR, or absolute; `truth` done / not_done / wrong;
optional `checks`, `mistake`, `lighting`, `camera`, `split` tuning / held_out, default held_out). Each frame runs
once, one at a time (honest latencies), as a `full` check on the bundled plan's step, judged against the step's end
frame in data/tutorials/. Reported numbers are from the held-out split. Step-plan accuracy (EVAL-01/02) comes from
eval/steps-*.json against the plan each file names (`plan`, relative to app/) or the bundled plan.
Costs ~$0.0025 per SAM request (the hand gate, then one per distinct object prompt) plus ~$0.003 per Muse Spark call.
"""

import argparse
import asyncio
import base64
import contextvars
import hashlib
import json
import math
import re
import statistics
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import sam, spark
from .checks import engine
from .config import APP, DATA, DIR, PRICES, RUNTIME
from .gateway import GatewayError, gateway, has_key

EVAL = APP / "eval"
RECORDED = EVAL / "recorded.jsonl"
PLANS = Path(__file__).parent / "fixtures" / "plans"
NAME = re.compile(r"step(\d+)-(done|wrong|unsure|waiting)\b")  # day-1 file names: step7-wrong-blue-in-rh.jpg


def load_labels(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def folder_labels(folder: Path, tutorial: str) -> list[dict]:
    """Day-1 labels: a `labels.json` in the folder ({"x.jpg": {"step", "expect", "tutorial"?}}) or the file name."""
    side = json.loads((folder / "labels.json").read_text(encoding="utf-8")) if (folder / "labels.json").exists() else {}
    out = []
    for f in sorted(folder.glob("*.jp*g")):
        lab = side.get(f.name)
        if lab is None and (m := NAME.search(f.name)):
            lab = {"step": int(m[1]), "expect": m[2]}
        if lab:
            truth = lab["expect"] if lab["expect"] in ("done", "wrong") else "not_done"
            out.append({"frame": str(f.resolve()), "tutorial_id": lab.get("tutorial", tutorial), "step_id": lab["step"],
                        "truth": truth, "labeller": "file name"})
    return out


def frame_path(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else next((b / p for b in (APP, RUNTIME) if (b / p).exists()), APP / p)


def plan_for(tid: str, cache: dict) -> dict:
    if tid not in cache:
        cache[tid] = json.loads((PLANS / f"{tid}.json").read_text(encoding="utf-8"))
    return cache[tid]


def step_of(label: dict, cache: dict) -> dict:
    return next(s for s in plan_for(label["tutorial_id"], cache)["steps"] if s["id"] == label["step_id"])


def estimate_usd(step: dict) -> float:
    """Rough: the hand gate + one SAM request per object (terminals share one), + a Muse Spark call with judged checks."""
    checks = [c for c in step.get("checks") or [] if c.get("enabled", True)]
    judged = [c for c in checks if c["kind"] == "judged" and not c.get("always_ask")]
    if not step.get("objects") and not judged and any(c.get("always_ask") for c in checks):
        return 0.0
    prompts = {("terminal" if engine.terminal_label(o["name"]) else o["sam_prompt"]) for o in step.get("objects") or []}
    return PRICES["sam_per_image"] * (1 + len(prompts)) + (0.003 if judged else 0.0)


def _pack(seg: dict) -> dict:
    return {**seg, "instances": [{"box": i["box"], "score": i["score"], "shape": list(i["mask"].shape),
                                  "mask": base64.b64encode(zlib.compress(np.packbits(i["mask"]).tobytes())).decode()}
                                 for i in seg["instances"]]}


def _unpack(seg: dict) -> dict:
    out = []
    for i in seg["instances"]:
        h, w = i["shape"]
        bits = np.unpackbits(np.frombuffer(zlib.decompress(base64.b64decode(i["mask"])), np.uint8))[:h * w]
        out.append({"box": i["box"], "score": i["score"], "mask": bits.reshape(h, w).astype(bool)})
    return {**seg, "instances": out}


def record_key(lab: dict, kind: str, ablation: str = "full") -> str:
    key = f"{lab['frame']}|{lab['tutorial_id']}|{lab['step_id']}|{lab.get('kind', kind)}"
    # sam_only re-scores the verify run's SAM answers (no new calls)
    return key + {"full": "", "verify": "|verify", "sam_only": "|verify", "spark_only": "|spark_only", "soft": "|soft"}[ablation]


def ablate(step: dict, ablation: str) -> dict:
    """Paper ablations. verify: the full check with the hand gate off (tutorial frames show the presenter's hands;
    the product waits for them to move away, which measures nothing). sam_only: verify, measured checks only (no Muse
    Spark). spark_only: one VLM, whole frame, no SAM at
    all (no hand gate, no objects): the step's judged checks plus "done looks like" as a judged check."""
    if ablation == "verify":
        return {**step, "actor": "none"}
    if ablation == "soft":  # the product's soft gate: the actor ("hand") outlined next to the objects
        return {**step, "actor": "hand"}
    if ablation == "sam_only":
        return {**step, "actor": "none", "checks": [c for c in step["checks"] if c["kind"] == "measured"]}
    if ablation == "spark_only":
        judged = [c for c in step["checks"] if c["kind"] == "judged" and not c.get("always_ask")]
        goal = {"kind": "judged", "type": "judged", "a": None, "b": None, "n": None, "side": None, "id": f"s{step['id']}goal",
                "text": step.get("done_looks_like") or step["instruction"], "confidence": 0.9, "needs_review": False, "enabled": True}
        return {**step, "objects": [], "actor": "none", "checks": [goal, *judged]}
    return step


RATE_S = 60 / 50  # the API allows 60 requests a minute; a frame fires up to 9 at once, so the batch run paces itself
_pace = {"next": 0.0}


async def _paced():
    """Start one request at most every RATE_S seconds (live batch runs only; the product never needs it)."""
    loop = asyncio.get_running_loop()
    now = loop.time()
    at = max(now, _pace["next"])
    _pace["next"] = at + RATE_S
    await asyncio.sleep(at - now)


def _recording(rec: dict, segment, judge):
    """Real calls; each answer (or its error code) is kept in `rec`."""
    async def seg(frame, concept, session_id=None):
        await _paced()
        try:
            s = await segment(frame, concept, session_id)
        except GatewayError as e:
            rec["segments"][concept] = {"error": e.code}
            raise
        rec["segments"][concept] = _pack(s)
        return s

    async def jdg(*a, **kw):
        await _paced()
        try:
            rec["judge"] = await judge(*a, **kw)
        except GatewayError as e:
            rec["judge"] = {"error": e.code, "cost_usd": getattr(e, "cost_usd", 0.0)}
            raise
        return rec["judge"]
    return seg, jdg


def _replaying(rec: dict):
    """Answers from `rec`, no network. Something the live run never asked for fails like a model error."""
    async def seg(frame, concept, session_id=None):
        s = rec["segments"].get(concept) or {"error": "not_recorded"}
        if "error" in s:
            raise GatewayError(s["error"])
        return _unpack(s)

    async def jdg(*a, **kw):
        j = rec.get("judge") or {"error": "not_recorded"}
        if "error" in j:
            raise GatewayError(j["error"])
        return j
    return seg, jdg


_CUR = contextvars.ContextVar("eval_answers")  # (segment, judge) of the frame this task is checking


async def _seg(*a, **kw):
    return await _CUR.get()[0](*a, **kw)


async def _jdg(*a, **kw):
    return await _CUR.get()[1](*a, **kw)


async def run(labels: list[dict], kind: str = "full", recorded: dict | None = None, ablation: str = "full",
              prior: dict | None = None, jobs: int = 1) -> tuple[list[dict], dict]:
    """recorded None: real calls, returning (rows, the answers to save); `prior` answers (an earlier live run) are
    reused for SAM concepts it already asked, so a plan change only pays for what's new. Else every answer comes from
    `recorded`. `jobs` frames run at once (the pacer keeps the requests under the rate limit)."""
    cache, kept = {}, {}
    real = sam.segment, spark.judge
    from .providers import end_box  # the app's own crop of the end frame, so the eval matches the product
    gate = asyncio.Semaphore(jobs)

    async def one(lab):
        key = record_key(lab, kind, ablation)
        if recorded is None:
            kept[key] = {"key": key, "segments": {}, "judge": None}
            seg, jdg = _recording(kept[key], *real)
            old = {c: v for c, v in ((prior or {}).get(key) or {}).get("segments", {}).items() if "error" not in v}
            if old:
                async def seg(frame, concept, session_id=None, _live=seg):
                    if concept in old:
                        kept[key]["segments"][concept] = old[concept]
                        return _unpack(old[concept])
                    return await _live(frame, concept, session_id)
            _CUR.set((seg, jdg))
        elif key in recorded:
            _CUR.set(_replaying(recorded[key]))
        else:
            sys.exit(f"No recorded answers for {key} in {RECORDED.name}: run it once with --live.")
        step = ablate(step_of(lab, cache), ablation)
        end = DATA / "tutorials" / lab["tutorial_id"] / "frames" / f"step-{lab['step_id']}.jpg"
        async with gate:
            res = await engine.run(step, frame_path(lab["frame"]).read_bytes(), lab.get("kind", kind),
                                   end_frame=end.read_bytes() if end.exists() else None,
                                   end_box=end_box(end, step) if end.exists() and step.get("objects") else None)
        print(line(lab, res), flush=True)
        return {"label": lab, "result": res}

    sam.segment, spark.judge = _seg, _jdg
    try:
        rows = await asyncio.gather(*(asyncio.create_task(one(lab)) for lab in labels))
        await asyncio.gather(*list(engine._abandoned), return_exceptions=True)  # their cost counts (and is kept) too
    finally:
        sam.segment, spark.judge = real
    return list(rows), kept


def load_recorded(path: Path) -> dict:
    return {r["key"]: r for r in load_labels(path)} if path.exists() else {}


def save_recorded(path: Path, kept: dict) -> None:
    allr = {**load_recorded(path), **kept}
    path.write_text("".join(json.dumps(r) + "\n" for r in allr.values()), encoding="utf-8")


def line(lab: dict, res: dict) -> str:
    ok = (res["verdict"] == "done") == (lab["truth"] == "done")
    measured = " ".join(f"{r['check_id']}={r['value']}{'✓' if r['pass'] else '✕'}" for r in res["results"] if r["kind"] == "measured")
    judged = "; ".join(f"{r['check_id']} {'pass' if r['pass'] else 'FAIL'} {r['confidence']} ({r['reason']})"
                       for r in res["results"] if r["kind"] == "judged")
    found = ", ".join(f"{o['name']}={'yes' if o['found'] else 'NO'}" for o in res["objects"])
    return (f"{'OK ' if ok else 'BAD'} {Path(lab['frame']).name} step {lab['step_id']}: truth {lab['truth']}, got "
            f"{res['verdict']}{' (' + res['fallback'] + ')' if res['fallback'] else ''} · {res['latency_ms']} ms "
            f"{res['stages_ms']} · ${res['cost_usd']:.4f}\n    {measured}  hint={res['hint']!r}\n    found: {found}"
            + (f"\n    judged: {judged}" if judged else ""))


# ---- the numbers (pure, so tests can feed rows) ----

def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if not n:
        return None
    p = k / n
    mid, half = (p + z * z / (2 * n)), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((mid - half) / (1 + z * z / n), 3), round((mid + half) / (1 + z * z / n), 3)]


def of(k: int, n: int, pct: bool = False) -> str | None:
    if not n:
        return None
    return f"{k} of {n}" + (f" ({round(100 * k / n)}%)" if pct else "")


def secs(ms: float | None) -> str | None:
    return None if ms is None else f"{ms / 1000:.1f} s"


def p90(values: list[float]) -> float | None:
    v = sorted(values)
    return v[min(len(v) - 1, math.ceil(0.9 * len(v)) - 1)] if v else None


def iou(a: dict, b: dict) -> float:
    """Overlap of two [start, end] spans over their union."""
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    union = max(a["end"], b["end"]) - min(a["start"], b["start"])
    return inter / union if union > 0 else 0.0


def step_plan_accuracy(files: list[Path], tolerance_s: float = 2.0) -> tuple[str | None, list[dict]]:
    """EVAL-01/02: a labelled step is matched by one planned step whose start and end are both within ±2 s and whose
    instruction says the same thing; out of the labelled steps, or the planned ones if there are more (an extra or
    split step counts against). ponytail: "the same thing" is word overlap ≥ 0.5 of the label's content words; the
    pairs are printed for a person to confirm."""
    def words(t: str) -> set:
        return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in {"the", "a", "an", "into", "in", "to", "of", "and", "on"}}
    hit = total = 0
    detail = []
    for f in files:
        truth = json.loads(f.read_text(encoding="utf-8"))
        plan = json.loads(((APP / truth["plan"]) if truth.get("plan") else PLANS / f"{truth['tutorial_id']}.json").read_text(encoding="utf-8"))
        used = set()
        for lab in truth["steps"]:
            match = next((s for s in plan["steps"] if s["id"] not in used
                          and abs(s["start"] - lab["start"]) <= tolerance_s and abs(s["end"] - lab["end"]) <= tolerance_s
                          and len(words(lab["instruction"]) & words(s["instruction"])) >= 0.5 * len(words(lab["instruction"]))), None)
            if match:
                used.add(match["id"])
            near = max(plan["steps"], key=lambda s: iou(s, lab), default=None)  # what the plan did there, matched or not
            detail.append({"file": f.name, "label": lab, "planned": match and {k: match[k] for k in ("id", "start", "end", "instruction")},
                           "nearest": near and {**{k: near[k] for k in ("id", "start", "end", "instruction")}, "iou": round(iou(near, lab), 2)}})
        hit += len(used)
        total += max(len(truth["steps"]), len(plan["steps"]))  # an extra planned step lowers the score
    return of(hit, total), detail


def config_hash() -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in DIR.rglob("*") if p.is_file() and p.suffix in (".toml", ".md", ".json")):
        h.update(f.relative_to(DIR).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:8]


def report(rows: list[dict], plan_acc: str | None = None, note: str | None = None, spent: float | None = None) -> dict:
    held = [r for r in rows if r["label"].get("split", "held_out") == "held_out"]
    lab = lambda r: r["label"]  # noqa: E731
    v = lambda r: r["result"]["verdict"]  # noqa: E731
    n = len(held)
    done_ok = sum((v(r) == "done") == (lab(r)["truth"] == "done") for r in held)
    not_done = [r for r in held if lab(r)["truth"] != "done"]
    done = [r for r in held if lab(r)["truth"] == "done"]
    mistakes = [r for r in held if lab(r).get("mistake") or lab(r)["truth"] == "wrong"]
    split = {"measured": [0, 0], "judged": [0, 0]}  # EVAL-06: per-check, where the labeller could tell
    for r in held:
        got = {x["check_id"]: x for x in r["result"]["results"]}
        for cid, truth in (lab(r).get("checks") or {}).items():
            if cid in got:
                s = split[got[cid]["kind"]]
                s[0] += got[cid]["pass"] == truth
                s[1] += 1
    called = [r["result"] for r in held if r["result"]["stages_ms"]]  # a model was called (not the no-call always-ask)
    lat = [c["latency_ms"] for c in called]
    groups = {}
    for key in ("lighting", "camera"):
        for g in sorted({lab(r).get(key) for r in held} - {None}):
            rs = [r for r in held if lab(r).get(key) == g]
            groups[g] = {"count": len(rs), "done_accuracy": of(sum((v(r) == "done") == (lab(r)["truth"] == "done") for r in rs), len(rs))}
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "note": note,
        "label_count": len(rows), "held_out_count": n, "tuning_count": len(rows) - n,
        "labeller_agreement": None,  # needs two labellers on the same 20 frames
        "step_plan_accuracy": plan_acc,
        "done_accuracy": of(done_ok, n), "done_accuracy_ci": wilson(done_ok, n),
        "done_accuracy_measured": of(*split["measured"], pct=True), "done_accuracy_judged": of(*split["judged"], pct=True),
        "wrong_advances": of(sum(v(r) == "done" for r in not_done), len(not_done)),
        "wrong_corrections": of(sum(v(r) == "wrong" for r in done), len(done)),
        "unsure_rate": of(sum(v(r) == "unsure" for r in held), n, pct=True),
        "mistake_catch_rate": of(sum(v(r) in ("wrong", "unsure") for r in mistakes), len(mistakes)),
        "latency_ms_p50": secs(statistics.median(lat)) if lat else None, "latency_ms_p90": secs(p90(lat)),
        "cost_per_session_usd": None,  # EVAL-12 needs recorded sessions; frames give the per-check cost below
        "cost_per_check_usd": f"${statistics.mean(c['cost_usd'] for c in called):.4f}" if called else None,
        "spent_usd": None if spent is None else round(spent, 4),
        "variation_flips_of_20": None,  # --live re-runs: not built
        "groups": groups,
        "config_hash": config_hash(),
        "frames": [{"frame": lab(r)["frame"], "case": lab(r).get("case"), "step_id": lab(r)["step_id"], "truth": lab(r)["truth"], "verdict": v(r),
                    "fallback": r["result"]["fallback"], "latency_ms": r["result"]["latency_ms"],
                    "cost_usd": r["result"]["cost_usd"]} for r in rows],
    }


def write(rep: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    day = rep["run_at"][:10]
    path, i = out / f"{day}.json", 2
    while path.exists():  # a second run the same day keeps the first (evaluation plan §3.5: report both runs)
        path, i = out / f"{day}-{i}.json", i + 1
    path.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m server.evaluate")
    src = ap.add_mutually_exclusive_group()  # neither: score the step plans only (EVAL-02), no frames, no calls
    src.add_argument("--labels", type=Path, help="GoldenLabel lines, e.g. eval/labels.jsonl")
    src.add_argument("--folder", type=Path, help="day-1 folder of step<n>-<verdict>-*.jpg (prints only)")
    ap.add_argument("--tutorial", default="install-thermostat", help="--folder: the tutorial (default install-thermostat)")
    ap.add_argument("--kind", default="full")
    ap.add_argument("--jobs", type=int, default=1, help="frames checked at once (live latencies then include queueing)")
    ap.add_argument("--plans", type=Path, help="plan folder instead of the bundled plans (e.g. eval/cfbench/plans-uncalibrated)")
    ap.add_argument("--ablation", default="full", choices=["full", "verify", "soft", "sam_only", "spark_only"])
    ap.add_argument("--steps", default=str(EVAL / "steps-*.json"), help="step-boundary labels (glob)")
    ap.add_argument("--note", help="said on S13 and in the report, e.g. what the frames are")
    ap.add_argument("--out", type=Path, default=EVAL / "reports")
    ap.add_argument("--dry-run", action="store_true", help="list the frames and the estimated cost; no model calls")
    ap.add_argument("--live", action="store_true", help="call Meta (costs money) and record its answers; default: "
                                                        "re-score from the recorded answers, no calls")
    ap.add_argument("--recorded", type=Path, default=RECORDED, help="recorded model answers (default eval/recorded.jsonl)")
    a = ap.parse_args(argv)
    if a.plans:
        global PLANS
        PLANS = a.plans
    if not (a.labels or a.folder):
        steps = sorted(Path(a.steps).parent.glob(Path(a.steps).name))
        if not steps:
            sys.exit(f"No step labels match {a.steps}.")
        acc, detail = step_plan_accuracy(steps)
        for d in detail:
            lab, p, n = d["label"], d["planned"], d["nearest"]
            print(f"{d['file']}: {lab['start']}-{lab['end']} {lab['instruction']!r} → " + (
                f"match {p['start']}-{p['end']} {p['instruction']!r}" if p else
                f"no match; nearest #{n['id']} {n['start']}-{n['end']} (Δstart {n['start'] - lab['start']:+.1f} s, "
                f"Δend {n['end'] - lab['end']:+.1f} s, IoU {n['iou']}) {n['instruction']!r}" if n else "no match"))
        return print(f"step plan accuracy: {acc}")
    labels = load_labels(a.labels) if a.labels else folder_labels(a.folder, a.tutorial)
    if not labels:
        sys.exit("No labelled frames found.")
    steps = sorted(Path(a.steps).parent.glob(Path(a.steps).name))
    acc, detail = step_plan_accuracy(steps) if steps else (None, [])
    for d in detail:
        p = d["planned"]
        print(f"step plan {d['file']}: {d['label']['start']}-{d['label']['end']} {d['label']['instruction']!r} → "
              + (f"{p['start']}-{p['end']} {p['instruction']!r}" if p else "no match"))
    cache = {}
    est = sum(estimate_usd(step_of(lab, cache)) for lab in labels)
    print(f"{len(labels)} frames, estimated ${est:.2f}" + (f"; step plan {acc}" if acc else ""))
    if a.dry_run:
        return
    if a.live:
        if not has_key():
            sys.exit("MODEL_API_KEY isn't set (set -a; . ./.env; set +a).")
        before = gateway.budget.snapshot()["total_usd"]
        rows, kept = asyncio.run(run(labels, a.kind, None, a.ablation, load_recorded(a.recorded), a.jobs))
        save_recorded(a.recorded, kept)
        print(f"recorded the model answers in {a.recorded}")
        rep = report(rows, acc, a.note, gateway.budget.snapshot()["total_usd"] - before)
        rep["ablation"] = a.ablation
    else:
        recorded = load_recorded(a.recorded)
        if not recorded:
            sys.exit(f"No recorded answers in {a.recorded} yet: run once with --live (about ${est:.2f}).")
        rows, _ = asyncio.run(run(labels, a.kind, recorded, a.ablation))
        note = "re-scored from recorded model answers (no calls; costs are the recorded run's, no latencies)"
        rep = report(rows, acc, f"{a.note} · {note}" if a.note else note, 0.0)
        rep.update(latency_ms_p50=None, latency_ms_p90=None, ablation=a.ablation)  # nothing was timed
    print("\n" + "\n".join(f"{k}: {rep[k]}" for k in ("done_accuracy", "done_accuracy_ci", "done_accuracy_measured",
          "done_accuracy_judged", "wrong_advances", "wrong_corrections", "unsure_rate", "mistake_catch_rate",
          "latency_ms_p50", "latency_ms_p90", "cost_per_check_usd", "spent_usd", "step_plan_accuracy")))
    if a.labels:
        print(f"wrote {write(rep, a.out)}")


if __name__ == "__main__":
    main()
