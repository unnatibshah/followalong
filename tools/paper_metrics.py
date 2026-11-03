"""The paper's tables from the CF-Bench reports (eval/reports/*.json with "ablation") and the step labels. No calls.

    cd app && .venv/bin/python tools/paper_metrics.py     # → eval/cfbench/summary.md and summary.json
"""

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from server.evaluate import iou, wilson  # noqa: E402

OUT = APP / "eval" / "cfbench"
CASES = ["done", "done_dim", "incomplete", "missing_part", "wrong_colour"]
NAMES = {"full": "Hard hand gate (previous product)", "soft": "Product: soft actor gate + SAM 3.1 + Muse Spark", "verify": "Verifier: SAM 3.1 + Muse Spark (ours)",
         "sam_only": "SAM 3.1 measured checks only", "spark_only": "Muse Spark only (whole frame, VLM-as-judge)",
         "verify_uncalibrated": "Verifier without plan self-calibration"}


def latest_reports() -> dict:
    out = {}
    for d, suffix in ((APP / "eval" / "reports", ""), (OUT / "reports-uncalibrated", "_uncalibrated")):
        for f in sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime):
            r = json.loads(f.read_text(encoding="utf-8"))
            if r.get("ablation") and "cfbench" in json.dumps(r["frames"][:1]):
                out[r["ablation"] + suffix] = r  # the newest run of each ablation wins
    return out


def score(frames: list[dict]) -> dict:
    """Binary task: is the step done? A positive is a `done` verdict. `waiting`/`unsure` count as not done (safe)."""
    pos = [f for f in frames if f["truth"] == "done"]
    neg = [f for f in frames if f["truth"] != "done"]
    tp = sum(f["verdict"] == "done" for f in pos)
    fp = sum(f["verdict"] == "done" for f in neg)
    tn = len(neg) - fp
    acc = (tp + tn) / len(frames)
    bal = (tp / len(pos) + tn / len(neg)) / 2
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / len(pos)
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    caught = sum(f["verdict"] in ("wrong", "unsure") for f in neg)  # flagged to the learner, not just withheld
    return {"n": len(frames), "accuracy": round(acc, 3), "accuracy_ci": wilson(tp + tn, len(frames)),
            "balanced_accuracy": round(bal, 3), "done_recall": round(rec, 3), "done_precision": round(prec, 3),
            "f1": round(f1, 3), "false_advance_rate": round(fp / len(neg), 3), "false_advances": f"{fp} of {len(neg)}",
            "mistakes_flagged": f"{caught} of {len(neg)}",
            "latency_p50_s": round(statistics.median(x["latency_ms"] for x in frames) / 1000, 2),
            "cost_per_check_usd": round(statistics.mean(x["cost_usd"] for x in frames), 4)}


def by_case(frames: list[dict]) -> dict:
    out = {}
    for c in CASES:
        fs = [f for f in frames if f.get("case") == c]
        if fs:
            ok = sum((f["verdict"] == "done") == (f["truth"] == "done") for f in fs)
            out[c] = {"n": len(fs), "correct": ok, "rate": round(ok / len(fs), 3),
                      "verdicts": dict(Counter(f["verdict"] for f in fs))}
    return out


def segmentation() -> dict:
    """Muse Spark's step plan vs the independent step labels: temporal IoU of each labelled step with its best plan
    step, recall at IoU ≥ 0.3 / 0.5, mean boundary error, step-count error."""
    ious, starts, ends, counts = [], [], [], []
    for f in sorted((APP / "eval").glob("steps-*.json")):
        truth = json.loads(f.read_text(encoding="utf-8"))
        plan = json.loads((APP / "server/fixtures/plans" / f"{truth['tutorial_id']}.json").read_text(encoding="utf-8"))
        counts.append(len(plan["steps"]) - len(truth["steps"]))
        for lab in truth["steps"]:
            best = max(plan["steps"], key=lambda s: iou(s, lab))
            ious.append(iou(best, lab))
            starts.append(abs(best["start"] - lab["start"]))
            ends.append(abs(best["end"] - lab["end"]))
    if not ious:  # no independent step labels for these tutorials (yet)
        return {}
    return {"labelled_steps": len(ious), "tutorials": len(counts), "mean_tiou": round(statistics.mean(ious), 3),
            "recall_at_0.3": round(sum(i >= 0.3 for i in ious) / len(ious), 3),
            "recall_at_0.5": round(sum(i >= 0.5 for i in ious) / len(ious), 3),
            "median_start_error_s": round(statistics.median(starts), 1),
            "median_end_error_s": round(statistics.median(ends), 1),
            "mean_abs_step_count_error": round(statistics.mean(abs(c) for c in counts), 2)}


def main() -> None:
    reps = latest_reports()
    base = json.loads((OUT / "baseline.json").read_text(encoding="utf-8")) if (OUT / "baseline.json").exists() else None
    summary = {"systems": {k: {"name": NAMES[k], **score(r["frames"]), "by_case": by_case(r["frames"]),
                               "spent_usd": r.get("spent_usd")} for k, r in reps.items()},
               "pixel_baseline": base, "segmentation": segmentation(),
               "latency": json.loads((OUT / "latency.json").read_text())["summary"] if (OUT / "latency.json").exists() else None}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    rows = ["| System | Acc. | Bal. acc. | Done recall | False advance | Mistakes flagged | $/check |",
            "|---|---|---|---|---|---|---|"]
    if base:
        rows.append(f"| Pixel similarity to end frame (LOTO threshold) | {base['loto_accuracy']} | | | "
                    f"{base['loto_wrong_advances']} | | $0 |")
    for k in ("spark_only", "sam_only", "verify_uncalibrated", "verify", "full", "soft"):
        if k in summary["systems"]:
            s = summary["systems"][k]
            rows.append(f"| {s['name']} | {s['accuracy']:.0%} | {s['balanced_accuracy']:.0%} | {s['done_recall']:.0%} | "
                        f"{s['false_advances']} ({s['false_advance_rate']:.0%}) | {s['mistakes_flagged']} | "
                        f"${s['cost_per_check_usd']} |")
    case_rows = ["| System | " + " | ".join(CASES) + " |", "|---" * (len(CASES) + 1) + "|"]
    for k, s in summary["systems"].items():
        case_rows.append(f"| {k} | " + " | ".join(f"{s['by_case'][c]['correct']}/{s['by_case'][c]['n']}"
                                                  if c in s["by_case"] else "–" for c in CASES) + " |")
    seg = summary["segmentation"]
    md = ("## CF-Bench results\n\n" + "\n".join(rows) + "\n\n### Correct per case\n\n" + "\n".join(case_rows)
          + ("\n\n### Step segmentation (Muse Spark plan vs independent labels)\n\n"
             + "\n".join(f"- {k}: {v}" for k, v in seg.items()) if seg else "") + "\n"
          + f"\n### Latency (unpaced, one check at a time)\n\n{summary['latency']}\n")
    (OUT / "summary.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
