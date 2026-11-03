"""Bundled replays from CF-Bench, so the demo runs with no camera, no key and no person in the loop.

Each step of the session is the real engine's answer (re-run offline from the recorded SAM 3.1 / Muse Spark answers of the
`soft` re-score in eval/cfbench/recorded-soft.jsonl: the product rule, actor outlined next to the objects) on a CF-Bench frame:

    with-mistakes   per step: a counterfactual mistake (wrong colour, missing part, unfinished) the engine called
                    `wrong`, then the learner "fixes" it (the finished frame), the check and its confirm re-check
    clean           per step: the finished frame only

    cd app
    .venv/bin/python tools/make_replays.py install-thermostat breadboard-led ...
"""

import asyncio
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from server import evaluate, recordings  # noqa: E402
from server.checks import engine  # noqa: E402
from server.config import JUDGED_PROMPT  # noqa: E402
from server.providers import end_box  # noqa: E402

sys.path.insert(0, str(APP / "tools"))
import random  # noqa: E402

import cfbench  # noqa: E402  (grab: a frame of the video; camera: the learner's camera on it)

FRAME_MS = 250  # the learner's camera in a live-coach replay: 4 frames a second, on a fixed grid the player rounds to

LABELS = APP / "eval" / "cfbench" / "labels.jsonl"
RECORDED = APP / "eval" / "cfbench" / "recorded-soft.jsonl"
MISTAKES = ("wrong_colour", "missing_part", "incomplete")


async def judge(lab: dict, rec: dict, kind: str, cache: dict) -> dict:
    """The engine on one frame, every model answer from the recording (no network)."""
    real = evaluate.sam.segment, evaluate.spark.judge
    evaluate.sam.segment, evaluate.spark.judge = evaluate._replaying(rec)
    try:
        step = evaluate.ablate(evaluate.step_of(lab, cache), "soft")  # the product rule: actor outlined, never waited for
        end = APP / "data" / "tutorials" / lab["tutorial_id"] / "frames" / f"step-{lab['step_id']}.jpg"
        res = await engine.run(step, (APP / lab["frame"]).read_bytes(), kind, end_frame=end.read_bytes(),
                               end_box=end_box(end, step) if step.get("objects") else None)
        # offline re-runs time nothing: put back the API latencies the live run recorded (object requests run in
        # parallel, so the stage is the slowest one; Muse Spark's is already in its recorded answer)
        segs = [v["latency_ms"] for k, v in rec["segments"].items() if k != "hand" and "latency_ms" in v]
        if "objects" in res["stages_ms"] and segs:
            res["stages_ms"]["objects"] = max(segs)
            res["latency_ms"] = sum(res["stages_ms"].values())
        return res
    finally:
        evaluate.sam.segment, evaluate.spark.judge = real


async def build(tid: str, name: str, mistakes: bool) -> str:
    labels = [lab for lab in evaluate.load_labels(LABELS) if lab["tutorial_id"] == tid]
    rec = evaluate.load_recorded(RECORDED)
    cache = {}
    plan = evaluate.plan_for(tid, cache)
    t0 = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)
    events, results, ms = [], [], 0
    frames = recordings.DATA / "recordings" / name / "frames"
    shutil.rmtree(frames.parent, ignore_errors=True)
    frames.mkdir(parents=True)

    def ev(type_, step=None, **data):
        at = (t0 + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace("+00:00", "Z")  # fixed width: sorts by time
        events.append({"session_id": f"rec-{name}", "at": at, "type": type_, "tutorial_id": tid, "step_id": step,
                       "mode": "live", "data": data})

    def show(lab):
        shutil.copy(APP / lab["frame"], frames / f"preview-{ms}.jpg")

    async def check(lab, step, n, kind, trigger):
        res = await judge(lab, rec[evaluate.record_key(lab, "full", "soft")], kind, cache)
        show(lab)
        ev("check" if kind == "full" else "recheck", step, kind=kind, check_no=n, trigger=trigger, verdict=res["verdict"])
        results.append({"step_id": step, "kind": kind, "check_no": n, "trigger": trigger, "frame": lab["frame"],
                        "prompt_version": JUDGED_PROMPT["version"], "result": res})
        return res

    summary = []
    ev("session_start", plan_hash=plan["plan_hash"])
    for st in plan["steps"]:
        sid = st["id"]
        by = {lab["case"]: lab for lab in labels if lab["step_id"] == sid}
        verdicts = {c: (await judge(lab, rec[evaluate.record_key(lab, "full", "soft")], "full", cache))["verdict"] for c, lab in by.items()}
        ms += 1000
        ev("step_play", sid, reason="first" if sid == 1 else "advance")
        ms += int((st["end"] - st["start"]) * 1000)
        ev("step_wait", sid)
        if "incomplete" in by:
            show(by["incomplete"])  # the learner at work
        n = 0
        bad = next((c for c in MISTAKES if verdicts.get(c) == "wrong"), None) if mistakes else None
        if bad:
            ms += 3000
            n += 1
            res = await check(by[bad], sid, n, "full", "still")
            ev("mistake_shown", sid, failed_checks=[x["check_id"] for x in res["results"] if not x["pass"]])
            ms += 7000  # the learner fixes it
        good = next((c for c in ("done", "done_dim") if verdicts.get(c) == "done"), None)
        if good:
            ms += 2500
            n += 1
            await check(by[good], sid, n, "full", "fixed" if bad else "still")
            ms += 1200
            n += 1
            res = await check(by[good], sid, n, "recheck", "recheck")
            if res["verdict"] != "done":  # the measured-only confirm disagreed: move on by hand, as a learner would
                ms += 1500
                ev("action", sid, name="next")
        else:  # the engine never said done on this step: the learner presses Next
            ms += 2500
            ev("action", sid, name="next")
        ms += 500
        ev("advance", sid)
        summary.append(f"{sid}:{bad or '-'}/{good or 'next'}")
    ev("session_end", reason="completed")
    d = recordings.BUNDLED / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    title = f"{plan_title(tid)} · {'with mistakes' if mistakes else 'clean run'} (CF-Bench, generated)"
    meta = {"id": name, "tutorial_id": tid, "title": title, "who": "cfbench", "volunteer_code": None,
            "consent_ref": None, "delete_on_request": False, "plan_hash": plan["plan_hash"],
            "prompt_version": JUDGED_PROMPT["version"], "recorded_at": events[0]["at"]}
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (d / "timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    return f"{name}: " + " ".join(summary)


WATCH_CACHE = APP / "eval" / "cfbench" / "watch-replay-cache.json"


async def looks(tid: str, step: dict, frames: list[dict], plan: dict) -> list[dict]:
    """The live watcher on each frame in order, with its memory of the previous looks (real Muse Spark calls,
    cached in eval/cfbench/watch-replay-cache.json so a rebuild is free and identical)."""
    from server import providers, voice
    cache = json.loads(WATCH_CACHE.read_text(encoding="utf-8")) if WATCH_CACHE.exists() else {}
    notes, said, out = [], [], []
    det = providers.tutorials.details(tid, "x")
    ctx = {**plan, "title": plan_title(tid), "actor": providers.tutorials.actor(tid, "x")}
    for i, lab in enumerate(frames):
        key = f"{lab['frame']}|{len(notes)}|{voice.WATCH_PROMPT['version']}"
        if key not in cache:
            end = APP / "data" / "tutorials" / tid / "frames" / f"step-{step['id']}.jpg"
            cache[key] = await voice.watch(ctx, det, step, {"recent": notes[-5:], "said": said[-3:], "elapsed_s": 8 + 6 * i,
                                                            "encourage": i == 0}, (APP / lab["frame"]).read_bytes(),
                                           end.read_bytes(), None)
            WATCH_CACHE.write_text(json.dumps(cache, indent=1) + "\n", encoding="utf-8")
        r = cache[key]
        if r.get("observation"):
            notes.append(r["observation"])
        if r.get("say"):
            said.append(r["say"])
        out.append(r)
    return out


async def build_live(tid: str, name: str) -> str:
    """A live-coach session: per step, the learner at work, then a mistake (wrong part / unfinished), a hand over
    the work, the fix; each frame gets the live watcher's real look (what it saw, what it said), and the fix goes
    through the grounded check like in the app."""
    labels = [lab for lab in evaluate.load_labels(LABELS) if lab["tutorial_id"] == tid]
    rec = evaluate.load_recorded(RECORDED)
    cache = {}
    plan = evaluate.plan_for(tid, cache)
    t0 = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
    events, results, ms = [], [], 0
    frames = recordings.DATA / "recordings" / name / "frames"
    shutil.rmtree(frames.parent, ignore_errors=True)
    frames.mkdir(parents=True)

    def ev(type_, step=None, **data):
        at = (t0 + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace("+00:00", "Z")  # fixed width: sorts by time
        events.append({"session_id": f"rec-{name}", "at": at, "type": type_, "tutorial_id": tid, "step_id": step,
                       "mode": "live", "data": data})

    def at_grid():
        return -(-ms // FRAME_MS) * FRAME_MS  # the next grid time, so a look's frame is never overwritten by motion

    def show(lab):
        shutil.copy(APP / lab["frame"], frames / f"preview-{at_grid()}.jpg")

    video = APP / "data" / "tutorials" / tid / "video.mp4"

    def work(dur_ms, v0, v1, seed):
        """The learner doing the step: the tutorial's own footage from v0 to v1 s, seen through a different camera."""
        nonlocal ms
        n = max(1, dur_ms // FRAME_MS)
        for i in range(n):
            img = cfbench.camera(cfbench.grab(video, v0 + (v1 - v0) * i / max(1, n - 1)), random.Random(seed), 0.6)
            img.save(frames / f"preview-{at_grid()}.jpg", quality=80)
            ms += FRAME_MS

    summary = []
    ev("session_start", plan_hash=plan["plan_hash"])
    for st in plan["steps"]:
        sid = st["id"]
        by = {lab["case"]: lab for lab in labels if lab["step_id"] == sid}
        # A person's session: unfinished, then the wrong part, then finished. Not the blurred "missing part" frames
        # (made for scoring; on screen they only look broken). The finished frame is the one of the two (normal, dim
        # light) whose recorded check passes, if either does; its verdict below is still the real one.
        fin = [by[c] for c in ("done", "done_dim") if c in by]
        verdicts = [(await judge(lab, rec[evaluate.record_key(lab, "full", "soft")], "full", cache))["verdict"] for lab in fin]
        fin = [lab for _, lab in sorted(zip(verdicts, fin), key=lambda x: x[0] != "done")]
        seq = [by[c] for c in ("incomplete", "wrong_colour") if c in by] + fin[:1]
        seen = await looks(tid, st, seq, plan)
        ms += 1000
        ev("step_play", sid, reason="first" if sid == 1 else "advance")
        ms += int((st["end"] - st["start"]) * 1000)
        ev("step_wait", sid)
        # The learner starts while the coach reads out the step's directions (and its safety line), then keeps working
        # between the coach's looks: the step's footage runs from its start to its finished moment across the turn.
        v0, v1 = st["start"], st.get("done_at") or st["end"]
        stops = [v0 + (v1 - v0) * (j + 1) / (len(seq) + 1) for j in range(len(seq) + 1)]
        work(6500, v0, stops[0], sid)
        n, said_any = 0, []
        for j, (lab, r) in enumerate(zip(seq, seen)):
            if j:
                work(2000, stops[j - 1], stops[j], sid)
            show(lab)
            ms += 2200  # a look takes ~1.5-3 s
            if lab["case"] in ("done", "done_dim") or r["status"] == "looks_done":
                if lab["case"] not in ("done", "done_dim"):
                    continue  # the watcher thought an unfinished frame was done: the grounded check isn't run on it here
                ev("coach", sid, status=r["status"], observation=r.get("observation", ""), say="", on_action=r.get("on_action"), substitute=r.get("substitute", ""))
                res = await judge(lab, rec[evaluate.record_key(lab, "full", "soft")], "full", cache)
                n += 1
                ev("check", sid, kind="full", check_no=n, trigger="check_me", verdict=res["verdict"])
                results.append({"step_id": sid, "kind": "full", "check_no": n, "trigger": "check_me", "frame": lab["frame"],
                                "prompt_version": JUDGED_PROMPT["version"], "result": res})
                if res["verdict"] == "done":
                    ms += 1500
                    n += 1
                    rr = await judge(lab, rec[evaluate.record_key(lab, "full", "soft")], "recheck", cache)
                    ev("recheck", sid, kind="recheck", check_no=n, trigger="recheck", verdict=rr["verdict"])
                    results.append({"step_id": sid, "kind": "recheck", "check_no": n, "trigger": "recheck",
                                    "frame": lab["frame"], "prompt_version": JUDGED_PROMPT["version"], "result": rr})
                    if rr["verdict"] != "done":
                        ms += 1500
                        ev("action", sid, name="next")
                else:
                    ms += 4000
                    ev("action", sid, name="next")
                ms += 4500  # "Yes, that's right! Next up..."
                break
            ev("coach", sid, status=r["status"], observation=r.get("observation", ""), say=r.get("say", ""), on_action=r.get("on_action"), substitute=r.get("substitute", ""))
            if r.get("say"):
                said_any.append(r["status"])
                ms += 300 * len(r["say"].split())  # let the line finish before the next thing happens
        else:
            ms += 2500
            ev("action", sid, name="next")
        ms += 500
        ev("advance", sid)
        summary.append(f"{sid}:{'/'.join(said_any) or '-'}")
    ev("session_end", reason="completed")
    d = recordings.BUNDLED / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    meta = {"id": name, "tutorial_id": tid, "title": f"{plan_title(tid)} · live coach (generated)", "who": "cfbench",
            "volunteer_code": None, "consent_ref": None, "delete_on_request": False, "plan_hash": plan["plan_hash"],
            "prompt_version": JUDGED_PROMPT["version"], "recorded_at": events[0]["at"]}
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (d / "timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    return f"{name}: " + " ".join(summary)


# A different person's video as the learner: the tutorial teaches, this footage is "you" (another rope, colour, table,
# hands). Every look and every check runs live on its frames; answers are cached so a rebuild is free and identical.
CROSS = {
    "figure-eight-knot": {
        "name": "figure-eight-pink-cord", "video": "https://www.youtube.com/watch?v=Sq3Ndf_jm_Y",
        "credit": "\u201cActivity at Home: Tie a Figure 8 Knot\u201d by Terrapin Adventures, CC BY",
        "title": "Tie a figure-eight knot",
        "steps": {1: (8.5, 18.0), 2: (18.0, 33.0), 3: (33.5, 44.5)}, "end": 47.6,  # the end card starts at 48 s
    },
    "tie-a-tie": {
        "name": "tie-a-tie-session", "video": "https://www.youtube.com/watch?v=S5rGTh-O7Wg",
        "credit": "\u201cTies for Fun Tutorial: The Half-Windsor\u201d by tiesforfun, CC BY",
        "title": "Tie a half Windsor knot", "steps": "auto",
    },
}
# Tried and left out (Nov 3): ASFN Fishing's figure-eight (a hook knot in fishing line: another task), BTC Biker's
# soldering (an extension wire and a ring terminal: another job), and gigametre's "Simple resistor/LED circuit" as breadboard practice footage. The camera is
# far back, hands cover the wires through step 1, and the LED is never lit, so the checks (rightly) never pass.

CROSS_DIR = APP / "eval" / "cross"


async def practice_steps(video: Path, src: Path, plan: dict, spec: dict) -> dict:
    """{tutorial step id: (start, end)} in the practice video: Muse Spark finds its steps (cached in steps.json), and
    they're lined up with the tutorial's in order: one to one when the counts match, otherwise by share of the work."""
    from server import tutorials as tut
    from server.gateway import gateway
    cached = src / "steps.json"
    if cached.exists():
        found = json.loads(cached.read_text(encoding="utf-8"))
    else:
        duration = (await tut.probe(video))["duration"]
        fid = await gateway.upload_file(video, 3600)
        found = [{k: s.get(k) for k in ("start", "end", "done_at", "instruction")} for s in await tut.find_steps(fid, duration, {})]
        cached.write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    if not found:
        raise SystemExit(f"Muse Spark found no steps in {video}: pick another practice video")
    lo, hi = spec.get("window", (0, 1e9))
    found = [s for s in found if s["start"] >= lo and (s.get("done_at") or s["end"]) <= hi] or found
    ours = plan["steps"]
    ends = lambda s: s.get("done_at") or s["end"]  # noqa: E731
    if len(found) == len(ours):
        return {o["id"]: (f["start"], ends(f)) for o, f in zip(ours, found)}
    t0, t1 = found[0]["start"], ends(found[-1])
    u0, u1 = ours[0]["start"], ends(ours[-1])
    k = (t1 - t0) / max(1e-6, u1 - u0)
    return {o["id"]: (t0 + (o["start"] - u0) * k, t0 + (ends(o) - u0) * k) for o in ours}


async def build_cross(key: str) -> str:
    from server import tutorials as tut
    spec = CROSS[key]
    tid = spec.get("tutorial", key)
    name = spec["name"]
    plan = evaluate.plan_for(tid, {})
    src = CROSS_DIR / name
    src.mkdir(parents=True, exist_ok=True)
    video = src / "video.mp4"
    if not video.exists():
        got = await asyncio.to_thread(tut.fetch_link, spec["video"], 600, src)
        Path(got["path"]).rename(video)
    if spec["steps"] == "auto":
        spec = {**spec, "steps": await practice_steps(video, src, plan, spec)}
        spec.setdefault("end", max(b for _, b in spec["steps"].values()) + 4)
    cache_f = src / "checks.json"
    checks = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}

    async def check(st: dict, frame_rel: str, kind: str) -> dict:
        key = f"{frame_rel}|{kind}|{JUDGED_PROMPT['version']}"
        if key not in checks:
            step = evaluate.ablate(evaluate.step_of({"tutorial_id": tid, "step_id": st["id"]}, {}), "soft")
            end = APP / "data" / "tutorials" / tid / "frames" / f"step-{st['id']}.jpg"
            checks[key] = await engine.run(step, (APP / frame_rel).read_bytes(), kind, end_frame=end.read_bytes(),
                                           end_box=end_box(end, step) if step.get("objects") else None)
            cache_f.write_text(json.dumps(checks, indent=1) + "\n", encoding="utf-8")
        return checks[key]

    t0 = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)
    events, results, ms = [], [], 0
    frames = recordings.DATA / "recordings" / name / "frames"
    shutil.rmtree(frames.parent, ignore_errors=True)
    frames.mkdir(parents=True)

    def ev(type_, step=None, **data):
        at = (t0 + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace("+00:00", "Z")  # fixed width: sorts by time
        events.append({"session_id": f"rec-{name}", "at": at, "type": type_, "tutorial_id": tid, "step_id": step,
                       "mode": "live", "data": data})

    def grid():
        return -(-ms // FRAME_MS) * FRAME_MS

    def grab(t: float):
        img = cfbench.grab(video, t)
        return img.crop(spec["crop"]).resize((1280, 720)) if spec.get("crop") else img

    kept: set[str] = set()

    def still(t: float, sid: int, tag: str) -> str:  # a look's frame, kept with the replay's sources
        rel = f"eval/cross/{name}/step{sid}-{tag}-{t:06.2f}.jpg"  # its footage time: a cached answer is about this picture
        kept.add(rel)
        grab(t).save(APP / rel, quality=88)
        shutil.copy(APP / rel, frames / f"preview-{grid()}.jpg")
        return rel

    # The practice footage never stops or jumps back, so the learner's video never freezes: the replay's clock is the
    # footage's, at real speed, except while a hint is read out (slowed, still moving).
    limit = spec.get("end") or max(b for _, b in spec["steps"].values()) + 4
    pos = 0.0

    def play_to(t: float, dur_ms: int | None = None):
        nonlocal ms, pos
        t = min(max(t, pos), limit)
        dur_ms = int((t - pos) * 1000) if dur_ms is None else dur_ms
        n = max(1, dur_ms // FRAME_MS)
        for i in range(1, n + 1):
            grab(pos + (t - pos) * i / n).save(frames / f"preview-{grid()}.jpg", quality=80)
            ms += FRAME_MS
        pos = t

    summary = []
    ev("session_start", plan_hash=plan["plan_hash"])
    # The setup the app does first: the coach looks at the work area, then says it's ready
    order = [st["id"] for st in plan["steps"]]
    first = spec["steps"][order[0]][0]
    # (over the step's first moment, slowed: the seconds before it are often the video's title card)
    pos = first
    grab(pos).save(frames / "preview-0.jpg", quality=80)
    ev("coach", 1, status="setup", observation="", say="Show me your hands and what you\u2019re working with.")
    play_to(first + 0.3, 3000)
    ev("coach", 1, status="setup", observation="", say="Perfect, I can see everything. Let\u2019s start.")
    play_to(first + 0.6, 2600)
    for st in plan["steps"]:
        sid = st["id"]
        a, b = spec["steps"][sid]
        if pos < a:  # the coach checked early: the footage catches up, quickly, while the step-done moment shows
            play_to(a, 1250)
        a = max(a, pos)  # an earlier step ran into this one's footage
        b = max(b, a + 4)
        # No watching phase: the tutorial loops the step, silently, beside the learner, who starts right away
        ev("step_play", sid, reason="first" if sid == 1 else "advance")
        ev("step_wait", sid)
        # four looks across the learner's own work on this step; the last on the finished moment
        # (as live: once the coach thinks it looks done, the check runs on that look's frame)
        rels, seen_at = [], []
        for j, f in enumerate((0.3, 0.55, 0.8, 1.0)):
            play_to(a + (b - a) * f)
            rels.append(still(pos, sid, f"look{j + 1}"))
            seen_at.append(ms)
            seen = await looks(tid, st, [{"frame": r} for r in rels], plan)
            if j >= 2 and seen[-1]["status"] == "looks_done":  # not before most of the step
                break
        # each look's answer about a second after its frame (the live coach's latency)
        stamp = ms
        n, said = 0, []
        for j, r in enumerate(seen):
            ms = min(stamp, seen_at[j] + 1000)
            last = j == len(seen) - 1
            ev("coach", sid, status=r["status"], observation=r.get("observation", ""),
               say="" if last else r.get("say", ""), on_action=r.get("on_action"), substitute=r.get("substitute", ""))
            if r.get("say") and not last:
                said.append(r["status"])
        ms = stamp
        res = await check(st, rels[-1], "full")
        n += 1
        ev("check", sid, kind="full", check_no=n, trigger="check_me", verdict=res["verdict"])
        results.append({"step_id": sid, "kind": "full", "check_no": n, "trigger": "check_me", "frame": rels[-1],
                        "prompt_version": JUDGED_PROMPT["version"], "result": res})
        frame_rel, verdicts = rels[-1], [res["verdict"]]
        # What the app does next, with the learner's own later footage: "not quite" → they keep going and it checks
        # again (each verdict real); "not sure" → it asks, and they say theirs looks like that.
        tries = 2
        while res["verdict"] in ("wrong", "waiting") and tries and pos + 1 < limit:
            tries -= 1
            play_to(pos + 1.0, 5500)  # the hint is read out while they keep fixing it
            frame_rel = still(pos, sid, f"fix{len(verdicts)}")
            res = await check(st, frame_rel, "full")
            n += 1
            ev("check", sid, kind="full", check_no=n, trigger="fixed", verdict=res["verdict"])
            results.append({"step_id": sid, "kind": "full", "check_no": n, "trigger": "fixed", "frame": frame_rel,
                            "prompt_version": JUDGED_PROMPT["version"], "result": res})
            verdicts.append(res["verdict"])
        # the coach talks over the rest of this step's footage, slowed, so the next step's footage isn't used up
        if res["verdict"] == "done":
            play_to(pos + 0.75, 1500)
            n += 1
            rr = await check(st, frame_rel, "recheck")
            ev("recheck", sid, kind="recheck", check_no=n, trigger="recheck", verdict=rr["verdict"])
            results.append({"step_id": sid, "kind": "recheck", "check_no": n, "trigger": "recheck", "frame": frame_rel,
                            "prompt_version": JUDGED_PROMPT["version"], "result": rr})
        elif res["verdict"] == "unsure":
            play_to(pos + 1, 4000)
            ev("ask_answer", sid, answer="yes")
        else:  # the check still says no: the coach offers the learner's word (as the app does), and they take it
            play_to(pos + 0.5, 3000)
            ev("coach", sid, status="setup", observation="", say="If yours does the same job, say \u201cit\u2019s right\u201d and we\u2019ll keep going.")
            play_to(pos + 0.7, 4000)
            ev("coach", sid, status="setup", observation="", say="Okay, you know your setup. Moving on.")
            play_to(pos + 0.5, 2500)
            ev("action", sid, name="next")
        ev("advance", sid)
        summary.append(f"{sid}:{'>'.join(verdicts)}/{'/'.join(said) or '-'}")
    play_to(limit, 4500)  # the ending: "You did it" over the learner's finished work
    ev("session_end", reason="completed")
    d = recordings.BUNDLED / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    meta = {"id": name, "tutorial_id": tid, "title": spec["title"], "who": "cross-video", "credit": spec["credit"],
            "volunteer_code": None, "consent_ref": None, "delete_on_request": False, "plan_hash": plan["plan_hash"],
            "prompt_version": JUDGED_PROMPT["version"], "recorded_at": events[0]["at"]}
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    # keep only this build's look frames and the cached answers about them
    for f in src.glob("step*.jpg"):
        if f"eval/cross/{name}/{f.name}" not in kept:
            f.unlink()
    cache_f.write_text(json.dumps({k: v for k, v in checks.items() if k.split("|")[0] in kept}, indent=1) + "\n", encoding="utf-8")
    wc = json.loads(WATCH_CACHE.read_text(encoding="utf-8"))
    WATCH_CACHE.write_text(json.dumps({k: v for k, v in wc.items() if not k.startswith(f"eval/cross/{name}/") or k.split("|")[0] in kept},
                                      indent=1) + "\n", encoding="utf-8")
    events.sort(key=lambda e: e["at"])  # looks are stamped at their frames' times, after the frames were made
    (d / "timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    return f"{name}: " + " ".join(summary)


def plan_title(tid: str) -> str:
    meta = json.loads((APP / "server" / "fixtures" / "tutorials.json").read_text(encoding="utf-8"))
    return next(t["title"] for t in meta if t["id"] == tid)


async def main(tids: list[str]) -> None:
    if tids and tids[0] == "--cross":  # a different person's video as the learner (CROSS)
        for key in tids[1:] or list(CROSS):
            print(await build_cross(key))
        return
    if tids and tids[0] == "--live":  # the live-coach replay (the demo): first tutorial named `with-mistakes`
        for i, tid in enumerate(tids[1:]):
            print(await build_live(tid, "with-mistakes" if i == 0 else f"{tid}-live-coach"))
        return
    for i, tid in enumerate(tids):
        # the first tutorial's mistakes replay is `with-mistakes`, the one S11 picks by default
        print(await build(tid, "with-mistakes" if i == 0 else f"{tid}-mistakes", True))
        print(await build(tid, f"{tid}-clean", False))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["install-thermostat"]))
