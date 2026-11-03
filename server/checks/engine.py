"""The live check (TD §3-4.3, FR-42-48): hand gate; then one SAM 3.1 request per step object and the Muse Spark judged
request, all in parallel; measured checks in code; the FR-46 verdict (CR-004: a measured fail is always `wrong`).
Returns a CheckResult (API spec §2).

Muse Spark runs only on a full check (the re-check is measured-only, FR-47; the browser carries the first check's
judged results over). Its answer is used only when every object was found and every measured check passed; otherwise
the check returns without waiting for it. A judged check with `always_ask: true` (the breaker: the camera can't see
it) is never sent; the step asks.
"""

import asyncio
import re
import time

from .. import sam, spark
from ..config import CHECKS, MODELS
from ..gateway import CapReached, GatewayError
from . import geometry as g

TERMINALS = CHECKS["terminals"]
# Muse Spark requests a check stopped waiting for. They run to the end so the gateway still counts their cost toward
# the caps (a cancelled HTTP request may still be billed, with no `usage` to count); the set keeps them referenced.
_abandoned: set = set()


COLOURS = ("red", "orange", "yellow", "green", "blue", "purple", "violet", "pink", "brown", "black", "white", "grey", "gray",
           "silver", "gold", "golden", "clear", "dark", "light", "bright", "pale", "striped")


def loose(prompt: str, step: dict) -> str | None:
    """The prompt without its colour words ("blue rope" → "rope"), unless the step needs the colour to tell two of its
    things apart (a red and a black clamp, a white and a blue wire). None when there's nothing to drop, or it matters.
    A wrong colour is still caught: the judged checks name it ("the blue LED")."""
    words = (prompt or "").split()
    rest = " ".join(w for w in words if w.lower() not in COLOURS)
    if not rest or rest == " ".join(words):
        return None
    others = [o.get("sam_prompt") or "" for o in step.get("objects") or []]
    same_kind = [q for q in others if q != prompt and " ".join(w for w in q.split() if w.lower() not in COLOURS) == rest]
    return None if same_kind else rest


# Everyday names for the same thing: a learner's thin cord tied in a knot is a "string" or a "knot" to the segmenter,
# not a "rope". Tried only when the plan's own word finds nothing. ponytail: a short hand list; grow it from misses.
SYNONYMS = {"rope": ["string", "cord", "knot"], "cord": ["string", "rope", "knot"], "string": ["cord", "rope"],
            "shoelace": ["lace", "string"], "wire": ["cable", "lead"], "cable": ["wire", "cord"], "screw": ["bolt"],
            "bolt": ["screw"], "tie": ["necktie"], "breadboard": ["board"], "clamp": ["clip"], "clip": ["clamp"],
            "battery": ["battery pack", "power supply"]}


# Wiring where a wire's colour is a code to follow (ground, neutral, hot, polarity): its colour is never relaxed
WIRING = re.compile(r"\b(breaker|mains|live|hot|neutral|ground|earth|volt\w*|polarity|positive|negative|terminal|plus|minus)\b", re.I)


def alternatives(prompt: str, step: dict) -> list[str]:
    """What to look for when `prompt` found nothing: without non-functional colours, then everyday synonyms."""
    if not (prompt or "").strip():
        return []
    noun = prompt.split()[-1].lower()
    text = " ".join([step.get("instruction", ""), step.get("done_looks_like", "")] + [c.get("text", "") for c in step.get("checks") or []])
    if noun in ("wire", "wires", "cable", "lead", "clamp", "clip") and WIRING.search(text) and any(w.lower() in COLOURS for w in prompt.split()):
        return []  # a green ground wire must not be "found" as the black hot one
    base = loose(prompt, step)
    if base is None and any(w.lower() in COLOURS for w in prompt.split()):
        return []  # the colour matters here (two wires of different colours): don't go looser
    noun = (base or prompt).split()[-1].lower()
    out = [base] if base else []
    out += [x for x in SYNONYMS.get(noun, []) if x not in out]
    return out


def terminal_label(name: str) -> str | None:
    label = name.removesuffix(" terminal")
    return label if label != name and label in TERMINALS["order"] else None


def verdict(all_found: bool, measured: list[dict], judged: list[dict], ask: bool = False,
            threshold: float = CHECKS["judged_confidence_threshold"]) -> str:
    """FR-46 with CR-004, in order. `ask`: the step has an always-ask judged check."""
    if not all_found:
        return "unsure"
    if any(not r["pass"] and r.get("type") in CHECKS["veto_types"] and r.get("veto", True) for r in measured):
        return "wrong"  # CR-004: code decides what it can measure, whatever the judged checks say
    if any(not r["pass"] for r in measured):
        return "unsure"  # a view- or model-specific check failed: the learner's setup may just differ, so ask
    if ask or any(r["confidence"] < threshold for r in judged):
        return "unsure"
    if any(not r["pass"] for r in judged):
        return "wrong"
    return "done"


def _result(verdict_: str, **kw) -> dict:
    base = {"verdict": verdict_, "fallback": None, "results": [], "hint": None, "objects": [], "hand_blocked": False,
            "latency_ms": 0, "cost_usd": 0.0, "models": [MODELS["segment"]], "stages_ms": {}}
    return base | kw


async def run(step: dict, frame: bytes, kind: str, session_id: str | None = None, end_frame: bytes | None = None,
              end_box: list[float] | None = None) -> dict:
    t0 = time.monotonic()
    spent, stages = [], {}

    def done(v: str, **kw) -> dict:
        return _result(v, latency_ms=round((time.monotonic() - t0) * 1000), cost_usd=round(sum(spent), 4),
                       stages_ms=stages, **kw)

    checks = [c for c in step.get("checks") or [] if c.get("enabled", True)]
    judged = [c for c in checks if c["kind"] == "judged"] if kind == "full" else []
    ask = any(c.get("always_ask") for c in judged)
    if ask and not step.get("objects") and not any(c["kind"] == "measured" for c in checks):
        return done("unsure", models=[])  # nothing the camera can check (the breaker): ask, without any model call

    objects = step.get("objects") or []
    prompt_of = {o["name"]: TERMINALS["prompt"] if terminal_label(o["name"]) else o["sam_prompt"] for o in objects}
    prompts = list(dict.fromkeys(prompt_of.values()))

    # The actor: who or what does the work (details.json `actor`, any noun phrase: "hand", "robot gripper",
    # "excavator bucket"; "none" when nothing is in view). It's outlined alongside the objects, never waited for first.
    actor = (step.get("actor") or "hand").strip()
    actor_prompt = None if actor.lower() == "none" or actor in prompts else actor

    async def segments() -> dict:  # one request per distinct prompt, in parallel; terminals share one prompt
        out = dict(zip(prompts, await asyncio.gather(*(sam.segment(frame, p, session_id) for p in prompts))))
        # The learner's things needn't match the tutorial's colours: a "blue rope" not found is looked for again as
        # "rope", unless the step itself needs that colour (wire codes, "the red clamp", "the blue LED").
        missing = [p for p in prompts if not (out[p] or {}).get("instances")]

        async def retry(p: str):  # the colour-free noun, then everyday names for the same thing, until one is found
            for q in alternatives(p, step):
                res = await sam.segment(frame, q, session_id)
                if res and res.get("instances"):
                    out[p] = res
                    return
        await asyncio.gather(*(retry(p) for p in missing))
        return out

    async def actor_seg():  # optional evidence: a failed actor request never fails the check
        try:
            return await sam.segment(frame, actor_prompt, session_id) if actor_prompt else None
        except CapReached:
            raise
        except GatewayError:
            return None

    def drop(task):  # a request we stop waiting for runs to the end, so the gateway still counts its cost
        if task and not task.done():
            _abandoned.add(task)
            task.add_done_callback(lambda f: (_abandoned.discard(f), f.cancelled() or f.exception()))

    # 1. Soft gate (replaces the hand gate): the actor and the step's objects are outlined in parallel. The actor's
    # pixels are taken out of the objects before measuring, and it only matters for the verdict when a needed object
    # can't be found while the actor covers the work area ("move your hand a little"). Works the same for a person's
    # hands, a robot gripper, or no actor at all.
    t1 = time.monotonic()
    actor_task = asyncio.ensure_future(actor_seg())
    early = asyncio.ensure_future(segments()) if prompts else None
    # 2. Muse Spark. A step with objects sends it the live frame cropped around them once SAM has found them (step 5):
    # small details like a wire tip at a printed terminal label vanish when the whole frame is shrunk to
    # `crop_max_px`. A step without objects has nothing to crop to, so it starts now, on the whole frame.
    send = [c for c in judged if not c.get("always_ask")]
    judging = None
    if send and not ask and not objects:
        judging = asyncio.create_task(_judge(step, send, frame, end_frame, None, session_id))

    def drop_judge():
        drop(judging)

    # 3. The object requests (already running, next to the actor's).
    try:
        segs = await early if early else {}
    except CapReached:
        drop_judge()
        drop(actor_task)
        raise
    except GatewayError:
        drop_judge()
        drop(actor_task)
        return done("waiting", fallback="sam_failed")
    hand = await actor_task
    spent.extend(s["cost_usd"] for s in segs.values())
    if hand:
        spent.append(hand["cost_usd"])
        stages["actor"] = hand["latency_ms"]
    if prompts:
        stages["objects"] = round((time.monotonic() - t1) * 1000)
    size = next((x["size"] for x in [*segs.values(), hand or {}] if x.get("size")), None)
    shape = (size[1], size[0]) if size else None
    actor_masks = [i["mask"] for i in (hand or {}).get("instances", [])]

    # 4. Measured checks in code. An object missing or a measured fail decides now, without waiting for Muse Spark.
    t2 = time.monotonic()
    occluder = g.union(actor_masks, shape) if actor_masks and shape else None
    m = await asyncio.to_thread(_measure, step, kind, prompt_of, segs, shape, occluder)
    stages["geometry"] = round((time.monotonic() - t2) * 1000)
    measured, hint = m["results"], m["hint"]
    if not m["all_found"] and shape and not step.get("hands_ok") \
            and g.hand_cover(actor_masks, shape, CHECKS["work_area_border"]) > CHECKS["hand_cover"]:
        drop_judge()  # a needed part is missing while the actor covers the work: it may be hiding it
        missing = ", ".join(o["name"] for o in m["objects"] if not o["found"])
        who = "hand" if actor == "hand" else actor
        return done("waiting", hand_blocked=True, objects=m["objects"],
                    hint=f"I can't see the {missing}. If your {who} is covering it, move it a little; "
                         f"if it isn't there yet, that's the part this step needs.")
    v = verdict(m["all_found"], measured, [], ask=ask)
    if not send or ask or v == "wrong" or not m["all_found"]:  # no Muse Spark call needed (or none started yet)
        drop_judge()
        return done(v, results=measured, objects=m["objects"], hint=hint if v != "done" else None)

    # 5. Measured checks passed: the judged checks decide (FR-46), on the crop around the objects when there are any.
    boxes = [o["box"] for o in m["objects"] if o["box"]]
    crop = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)] \
        if boxes else None
    j = await (judging or _judge(step, send, frame, end_frame, crop, session_id, end_box))
    spent.append(j["cost_usd"])
    if "results" not in j:  # Muse Spark failed (FR-150): ask
        return done("unsure", fallback="judge_failed", results=measured, objects=m["objects"], hint=hint)
    stages["judge"] = j["latency_ms"]
    v = verdict(True, measured, j["results"])
    return done(v, results=measured + j["results"], objects=m["objects"],
                models=[MODELS["segment"], MODELS["judged"]],
                hint=j["hint"] or hint if v == "wrong" else None)  # TD §4.3: Muse Spark's hint for a judged fail


async def _judge(step, checks, frame, end_frame, box, session_id, end_box=None) -> dict:
    """spark.judge, or {"error", "cost_usd"} on a failure: never raises (FR-150)."""
    try:
        if end_frame is None:
            raise GatewayError("no_end_frame")
        # the end frame cropped around the same objects (its build-time outlines), so both pictures match in scale
        return await spark.judge(step, checks, frame, end_frame, box, session_id, **({"end_box": end_box} if end_box else {}))
    except GatewayError as e:
        return {"error": e.code, "cost_usd": getattr(e, "cost_usd", 0.0)}
    except Exception as e:  # a frame Pillow can't read: still the judge_failed fallback, never a crash
        return {"error": type(e).__name__, "cost_usd": 0.0}


def _measure(step: dict, kind: str, prompt_of: dict, segs: dict, shape, occluder=None) -> dict:
    """`occluder`: the actor's pixels (hands, a gripper), taken out of every object before measuring."""
    shape = shape or next(((s["size"][1], s["size"][0]) for s in segs.values() if s["size"]), (720, 1280))
    zones, hint = None, None
    sized = sizes(step.get("objects") or [], prompt_of, segs, shape)
    if any(terminal_label(n) for n in prompt_of):
        found = terminal_screws(segs[TERMINALS["prompt"]]["instances"], shape)
        zones = g.terminal_zones(found, TERMINALS["order"], TERMINALS["zone_width"], TERMINALS["zone_below"])
        if zones is None:
            hint = (f"I found {len(found)} terminals, not {len(TERMINALS['order'])}. "
                    "Move the camera so every terminal screw shows.")

    insts, masks, objects = {}, {}, []
    for name, prompt in prompt_of.items():
        label = terminal_label(name)
        if label:
            ok = zones is not None
            box = zones[label] if ok else None
            insts[name] = [{"box": box, "score": 1.0}] if ok else []
            if ok:
                masks[name] = g.rect_mask(box, shape)
            objects.append({"name": name, "found": ok, "box": [int(round(v)) for v in box] if ok else None,
                            "outline_svg": g.rect_svg(box) if ok else None})
            continue
        found = sized[name]
        insts[name] = found
        if found:
            masks[name] = g.union([i["mask"] for i in found], shape)
            if occluder is not None and prompt != step.get("actor") and (masks[name] & ~occluder).any():
                masks[name] = masks[name] & ~occluder
        objects.append({"name": name, "found": bool(found), "box": g.box_of(masks[name]) if found else None,
                        "outline_svg": "".join(g.outline_svg(i["mask"]) for i in found) or None})

    checks = [c for c in step.get("checks") or [] if c.get("enabled", True)]
    measured = [c for c in checks if c["kind"] == "measured"]
    if kind != "full" and not measured:  # FR-47: a re-check of a judged-only step checks each object is present
        measured = [{"id": f"present:{o}", "type": "present", "a": o} for o in prompt_of]

    results = []
    for c in measured:
        a, b = c.get("a"), c.get("b")
        if (c["type"] not in ("present", "count") and a not in masks) or (b and b not in masks):
            continue  # an object wasn't found: the verdict is unsure, nothing to measure
        r = _check(c, insts, masks, zones)
        results.append({"check_id": c["id"], "type": c["type"], "kind": "measured", **r,
                        # CR-006: a check calibrated on a manual's drawing asks, never vetoes, on a real photo
                        **({"veto": False} if step.get("manual_page") else {})})
        if not r["pass"] and hint is None:
            hint = _hint(c, r, masks, zones)

    return {"results": results, "objects": objects, "hint": hint, "all_found": all(o["found"] for o in objects)}


def terminal_screws(instances: list[dict], shape) -> list[dict]:
    """The terminal prompt's instances in the configured size band (share of the frame)."""
    return g.size_filter(instances, TERMINALS, shape[0] * shape[1], 0, 1)


def sizes(objects: list[dict], prompt_of: dict, segs: dict, shape) -> dict:
    """{object name: its instances after the size filter} for every non-terminal object (FR-44). Objects with an
    `area_ref` are filtered after the others, against their reference's filtered outline."""
    cfg, out = CHECKS["size"], {}
    for o in sorted((o for o in objects if not terminal_label(o["name"])), key=lambda o: bool(o.get("area_ref"))):
        ref = o.get("area_ref")
        base = int(g.union([i["mask"] for i in out.get(ref, [])], shape).sum()) if ref else shape[0] * shape[1]
        out[o["name"]] = g.size_filter(segs[prompt_of[o["name"]]]["instances"], o, base, cfg["aspect"], cfg["tie_ratio"])
    return out


def _check(c: dict, insts: dict, masks: dict, zones: dict | None) -> dict:
    t, a, b = c["type"], c.get("a"), c.get("b")
    if t == "present":
        return g.present(insts.get(a, []), CHECKS["sam_min_confidence"])
    if t == "count":
        return g.count(insts.get(a, []), c["n"])
    if t == "inside":
        wire = g.wire_end(masks[a], zones) if zones and terminal_label(b) else masks[a]
        return g.inside(wire, masks[b], CHECKS["inside"])
    if t == "adjacent":
        return g.adjacent(masks[a], masks[b], c.get("side"), CHECKS["adjacent"])
    if t == "left_of":
        return g.left_of(masks[a], masks[b], CHECKS["direction_margin"])
    if t == "above":
        return g.above(masks[a], masks[b], CHECKS["direction_margin"])
    if t == "area_ratio":
        return g.area_ratio(masks[a], masks[b], c.get("ratio") or 0, CHECKS["area_ratio"])
    raise ValueError(f"unknown measured check type {t!r}")


def _hint(c: dict, r: dict, masks: dict, zones: dict | None) -> str:
    a, b = c.get("a"), c.get("b")
    if c["type"] == "inside" and zones and terminal_label(b):
        end = g.wire_end(masks[a], zones)
        shape = end.shape
        best = max(zones, key=lambda lab: g.inside(end, g.rect_mask(zones[lab], shape), 0)["value"])
        if best != terminal_label(b) and g.inside(end, g.rect_mask(zones[best], shape), CHECKS["inside"])["pass"]:
            why = TERMINALS.get("why", {}).get(best)  # the safety reason, as the scripted copy gives it (DEF-011)
            return f"{a[:1].upper()}{a[1:]} goes in {terminal_label(b)}, not {best}." + (f" {why}" if why else "")
    return {
        "present": f"I can't see the {a}.",
        "count": f"I count {r['value']} of the {a}, not {c.get('n')}.",
        "inside": f"Your {a} isn't in the {b} yet.",
        "adjacent": f"Your {a} isn't next to the {b} yet.",
        "left_of": f"Your {a} should be left of the {b}.",
        "above": f"Your {a} should be above the {b}.",
        "area_ratio": f"Your {a} doesn't look the right size next to the {b}.",
    }[c["type"]]

