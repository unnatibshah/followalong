"""CF-Bench: counterfactual learner frames made from the tutorial video itself, labelled by construction (no human).

For every step of every bundled tutorial (plan start / done_at, build-time SAM boxes in refs/step-<n>.json):

    done            the finished frame (done_at), camera shifted (crop, tilt, exposure, noise, JPEG)  truth done
    done_dim        the same frame, darker and shifted further                                       truth done
    incomplete      a frame 20% into the step: the work isn't done yet                               truth not_done
    missing_part    the finished frame with the step's main object blurred out                       truth not_done
    wrong_colour    the finished frame with red and blue swapped around the step's objects
                    (only steps whose text names a colour: the wrong wire / LED / band)              truth wrong

A negative that barely differs from its done frame (mean grey change < MIN_CHANGE in the object box) is dropped:
label noise filtered by measurement, not by a person.

    cd app
    .venv/bin/python tools/cfbench.py make          # frames → eval/cfbench/, labels → eval/cfbench/labels.jsonl
    .venv/bin/python tools/cfbench.py baseline      # pixel-similarity baseline (no model), leave-one-tutorial-out
    .venv/bin/python -m server.evaluate --labels eval/cfbench/labels.jsonl --live --note "CF-Bench"
"""

import json
import random
import re
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from server.tutorials import ffmpeg  # noqa: E402

PLANS = APP / "server" / "fixtures" / "plans"
TUT = APP / "data" / "tutorials"
OUT = APP / "eval" / "cfbench"
COLOURS = re.compile(r"\b(red|blue|green|orange|brown|white|black|yellow|striped)\b", re.I)
MIN_CHANGE = 6.0  # mean |Δgrey| (0-255) in the object box; below it the negative isn't visibly different


def grab(video: Path, t: float) -> Image.Image:
    jpg = subprocess.run([ffmpeg(), "-loglevel", "error", "-ss", f"{max(0, t):.2f}", "-i", str(video), "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "2", "-"], capture_output=True, check=True).stdout
    return Image.open(BytesIO(jpg)).convert("RGB")


def camera(img: Image.Image, rng: random.Random, strength: float = 1.0) -> Image.Image:
    """A different camera on the same scene: zoom/crop, small tilt, exposure, sensor noise; JPEG on save."""
    w, h = img.size
    s = 1 - rng.uniform(0.03, 0.08) * strength
    cw, ch = int(w * s), int(h * s)
    x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
    img = img.crop((x, y, x + cw, y + ch)).resize((w, h), Image.BICUBIC)
    img = img.rotate(rng.uniform(-3, 3) * strength, resample=Image.BICUBIC, fillcolor=(0, 0, 0))
    a = np.asarray(img, dtype=np.float32) + np.random.default_rng(rng.randint(0, 9999)).normal(0, 3 * strength, (h, w, 3))
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def boxes(tid: str, sid: int, size: tuple[int, int]) -> list[list[int]]:
    ref = TUT / tid / "refs" / f"step-{sid}.json"
    if not ref.exists():
        return []
    r = json.loads(ref.read_text(encoding="utf-8"))
    fs = r.get("frame_size") or size
    fx, fy = size[0] / fs[0], size[1] / fs[1]
    return [[int(o["box"][0] * fx), int(o["box"][1] * fy), int(o["box"][2] * fx), int(o["box"][3] * fy)]
            for o in r["objects"] if o.get("found") and o.get("box")]


def union(bs: list[list[int]], size: tuple[int, int], pad: float = 0.1) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)
    px, py = (x1 - x0) * pad + 4, (y1 - y0) * pad + 4
    return max(0, int(x0 - px)), max(0, int(y0 - py)), min(size[0], int(x1 + px)), min(size[1], int(y1 + py))


def blur_out(img: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Crude inpaint: the box filled with a heavy blur of itself and its surroundings (the part is gone)."""
    out = img.copy()
    w, h = box[2] - box[0], box[3] - box[1]
    big = (max(0, box[0] - w), max(0, box[1] - h), min(img.width, box[2] + w), min(img.height, box[3] + h))
    blurred = img.crop(big).filter(ImageFilter.GaussianBlur(max(w, h) / 3))
    out.paste(blurred.crop((box[0] - big[0], box[1] - big[1], box[2] - big[0], box[3] - big[1])), box[:2])
    return out


def swap_rb(img: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    out = img.copy()
    r, g, b = img.crop(box).split()
    out.paste(Image.merge("RGB", (b, g, r)), box[:2])
    return out


def change(a: Image.Image, b: Image.Image, box) -> float:
    ga, gb = (np.asarray(x.crop(box).convert("L").resize((64, 64)), dtype=np.float32) for x in (a, b))
    return float(np.abs(ga - gb).mean())


def make() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    labels, dropped = [], []
    for pf in sorted(PLANS.glob("*.json")):
        plan = json.loads(pf.read_text(encoding="utf-8"))
        tid, video = plan["tutorial_id"], TUT / plan["tutorial_id"] / "video.mp4"
        (OUT / "frames" / tid).mkdir(parents=True, exist_ok=True)
        for st in plan["steps"]:
            sid, rng = st["id"], random.Random(f"{tid}-{st['id']}")
            done_t = st.get("done_at") or st["end"]
            fin = grab(video, done_t)
            bs = boxes(tid, sid, fin.size)
            box = union(bs, fin.size) if bs else (fin.width // 4, fin.height // 4, 3 * fin.width // 4, 3 * fin.height // 4)
            text = " ".join([st["instruction"], st.get("done_looks_like") or ""] + [c["text"] for c in st["checks"]])
            cases = [("done", "done", None, camera(fin, rng), "normal"),
                     ("done_dim", "done", None, ImageEnhance.Brightness(camera(fin, rng, 1.5)).enhance(0.6), "dim")]
            early = grab(video, st["start"] + 0.2 * (done_t - st["start"]))
            cases.append(("incomplete", "not_done", "step not finished", camera(early, rng), "normal"))
            if bs:
                main = union([bs[0]], fin.size, 0.15)
                cases.append(("missing_part", "not_done", "part missing", camera(blur_out(fin, main), rng), "normal"))
            if COLOURS.search(text) and bs:
                cases.append(("wrong_colour", "wrong", "wrong colour part", camera(swap_rb(fin, box), rng), "normal"))
            for name, truth, mistake, img, light in cases:
                if truth != "done" and change(img, fin, box) < MIN_CHANGE:
                    dropped.append(f"{tid} step {sid} {name}")
                    continue
                rel = Path("eval/cfbench/frames") / tid / f"step{sid}-{name}.jpg"
                img.save(APP / rel, quality=85)
                labels.append({"frame": rel.as_posix(), "tutorial_id": tid, "step_id": sid, "truth": truth,
                               "case": name, "mistake": mistake, "lighting": light, "camera": "shifted",
                               "split": "held_out", "labeller": "cfbench (by construction)"})
        print(f"{tid}: {sum(lab['tutorial_id'] == tid for lab in labels)} frames", flush=True)
    (OUT / "labels.jsonl").write_text("".join(json.dumps(x) + "\n" for x in labels), encoding="utf-8")
    print(f"{len(labels)} frames → {OUT / 'labels.jsonl'}; dropped as too similar: {dropped or 'none'}")


def baseline() -> None:
    """No model: 'done' if the frame looks like the step's end frame (cosine of 64x36 grey, mean-removed) above a
    threshold. The threshold is picked on the other tutorials (leave-one-tutorial-out), so it never sees its test."""
    labels = [json.loads(x) for x in (OUT / "labels.jsonl").read_text(encoding="utf-8").splitlines() if x]

    def vec(p: Path) -> np.ndarray:
        v = np.asarray(Image.open(p).convert("L").resize((64, 36)), dtype=np.float32).ravel()
        v -= v.mean()
        return v / (np.linalg.norm(v) + 1e-6)
    for lab in labels:
        lab["sim"] = float(vec(APP / lab["frame"]) @ vec(TUT / lab["tutorial_id"] / "frames" / f"step-{lab['step_id']}.jpg"))
    acc = lambda rows, th: sum((r["sim"] >= th) == (r["truth"] == "done") for r in rows)  # noqa: E731
    grid = [i / 100 for i in range(0, 101)]
    ok = fa = neg = 0
    for tid in sorted({lab["tutorial_id"] for lab in labels}):
        train = [r for r in labels if r["tutorial_id"] != tid]
        test = [r for r in labels if r["tutorial_id"] == tid]
        th = max(grid, key=lambda t: acc(train, t))
        ok += acc(test, th)
        fa += sum(r["sim"] >= th for r in test if r["truth"] != "done")
        neg += sum(r["truth"] != "done" for r in test)
    best = max(grid, key=lambda t: acc(labels, t))
    res = {"n": len(labels), "loto_accuracy": f"{ok} of {len(labels)} ({100 * ok / len(labels):.0f}%)",
           "loto_wrong_advances": f"{fa} of {neg}", "oracle_threshold": best,
           "oracle_accuracy": f"{acc(labels, best)} of {len(labels)}"}
    (OUT / "baseline.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2))


def soft() -> None:
    """Answers for the soft-gate rule without new calls: the actor ("hand") outline from the product run, the object
    outlines and Muse Spark answers from the verify run, per frame → recorded-soft.jsonl (keys `|soft`)."""
    from server import evaluate
    rec = evaluate.load_recorded(OUT / "recorded-full.jsonl")
    out, missing = {}, 0
    for lab in evaluate.load_labels(OUT / "labels.jsonl"):
        full, ver = rec.get(evaluate.record_key(lab, "full")), rec.get(evaluate.record_key(lab, "full", "verify"))
        if not ver:
            missing += 1
            continue
        # hands_ok steps never asked for a hand in the product run: no actor outline (the soft gate never waits there)
        hand = {"hand": full["segments"]["hand"]} if full and "hand" in full["segments"] else {}
        key = evaluate.record_key(lab, "full", "soft")
        out[key] = {"key": key, "segments": {**ver["segments"], **hand}, "judge": ver["judge"]}
    evaluate.save_recorded(OUT / "recorded-soft.jsonl", out)
    print(f"{len(out)} frames merged, {missing} without both runs")


if __name__ == "__main__":
    {"make": make, "baseline": baseline, "soft": soft}[sys.argv[1] if len(sys.argv) > 1 else "make"]()
