"""Safety tiers per step, from the step's spoken `safety` line (server/details.py). Rules, not a model: the tier
decides whether a learner may continue, so it must be predictable, testable and err on the side of caution.

  gate     the step needs the learner to MAKE something safe first (power off at the breaker, prove dead, unplug).
           The lesson stops until they confirm, by voice or a tap. A camera can't see electricity, so we never infer it.
  danger   electricity, car batteries and clamps, moving or spinning parts, flames: the warning is spoken and the step
           never advances on the camera's word alone; the learner confirms it's finished.
  caution  any other hazard line (sharp, hot, pinch): spoken before the step, nothing blocks.
  ""       no hazard.

"Keep the breaker off" is a reminder, not a new action: it's danger, not another gate (gate fatigue makes people tap
through every prompt, which is worse than fewer, meaningful ones).
"""

import re

GATE = re.compile(
    r"\b((switch|turn|shut|flip)\w*\s+(\w+\s+){0,8}off|prove\w*\s+(it\s+)?dead|test\w*\s+(it\s+)?dead|"
    r"(confirm|check|ensure|make\s+sure|verify)\w*\s+(\w+\s+){0,5}(off|dead)|shows?\s+no\s+power|unplug\w*|disconnect\w*\s+the\s+(power|battery)|"
    r"(kill|cut|isolate|de-?energi[sz]e)\w*\s+(\w+\s+){0,3}(power|circuit|electricity|supply))\b", re.I)
DANGER = re.compile(
    r"\b(breaker|live|mains|volt\w*|electric\w*|shock|tester|power|dead|circuit|wires?\s+(are|is)\s+live|clamps?|jump\w*|"
    r"crank\w*|spinning|fans?|belts?|moving\s+parts|flame|gas|fuel|ladder|"
    # manual warnings (CR-006): furniture that can fall, and two-person lifts (not "anchor the rope", "two people can")
    r"tip\w*[\s-]+over|topple\w*|(anchor|fix|attach|secure|mount)\w*\s+(it\s+|this\s+|the\s+\w+\s+)?(to|in|into)\s+"
    r"the\s+(wall|studs?)|wall\s+anchors?|(needs?|requires?|takes?|with)\s+two\s+(people|persons|adults)|"
    r"two\s+(people|persons|adults)\s+(are\s+)?(needed|required))\b", re.I)

# A step that MAKES it safe, however it's worded: the breaker, power or mains, and "off", in one sentence
POWER_OFF = re.compile(r"\b(breaker|power|mains|circuit)\b[^.!?]{0,80}\boff\b|\boff\b[^.!?]{0,40}\b(breaker|power|mains|circuit)\b", re.I)
ELECTRIC = re.compile(r"\b(breaker|live|mains|volt\w*|electric\w*|shock|tester|power|wires?)\b", re.I)
# House wiring anywhere in a lesson (however each step is worded): every step is at least `danger`
MAINS = re.compile(r"\b(breaker|mains|live\s+wires?|hot\s+wires?|wall\s+box|circuit\s+panel|fuse\s+box)\b", re.I)
# A car battery lesson: jumper or booster cables, a car battery; or battery posts with a car in the lesson. (A 9 V
# battery's plus terminal on a breadboard is not a car battery.)
CAR_BATTERY = re.compile(r"\b(jump[\s-]?start\w*|jumper\s+cables?|booster\s+cables?|car\s+batter(y|ies))\b", re.I)
BATTERY_POSTS = re.compile(r"\b(plus|minus|positive|negative|battery)\s+(post|terminal)s?\b", re.I)
CAR = re.compile(r"\b(car|cars|engine|hood|bonnet|vehicle|truck)\b", re.I)


def tier(safety: str) -> str:
    text = safety.strip() if isinstance(safety, str) else ""  # a model can send a list or null
    if not text:
        return ""
    for line in re.split(r"(?<=[.!?])\s+", text):  # "Keep ..." is a reminder, but only for its own sentence
        if GATE.search(line) and not re.match(r"keep\b", line, re.I):
            return "gate"
    return "danger" if DANGER.search(text) else "caution"


def annotate(steps: dict) -> dict:
    """details.json `steps` with `hazard` added to each step (copies; the file is never rewritten).

    A step whose own direction is making it safe ("Flip the breaker to off") is a gate even if its safety line talks
    about something else. And in a lesson with an electrical gate anywhere, no step is merely `caution` or unmarked:
    every one is at least `danger`, so the camera alone never advances it, whatever the model chose to warn about."""
    keys = sorted((k for k, v in steps.items() if isinstance(v, dict)), key=lambda k: int(k) if str(k).isdigit() else 0)
    out = {}
    for k in keys:
        v = steps[k]
        t = tier(v.get("safety", ""))
        say = f"{v.get('title') or ''}. {v.get('say') or ''}"  # its title and its spoken direction
        if t != "gate" and (GATE.search(say) or POWER_OFF.search(say)) and ELECTRIC.search(say + " " + str(v.get("safety") or "")):
            t = "gate"
        out[k] = {**v, "hazard": t}
    text = lambda o: " ".join(str(o.get(f) or "") for f in ("title", "say", "safety"))  # noqa: E731
    everything = " ".join(text(o) for o in out.values())
    car = CAR_BATTERY.search(everything) or (BATTERY_POSTS.search(everything) and CAR.search(everything))
    if any(o["hazard"] == "gate" and ELECTRIC.search(text(o)) for o in out.values()) or MAINS.search(everything) or car:
        for o in out.values():
            if o["hazard"] in ("", "caution"):
                o["hazard"] = "danger"
    return out


def lesson(steps: dict) -> dict:
    """The lesson's notice: its highest tier (the same per-step tiers annotate() gives) and whether it involves
    electricity."""
    hazards = annotate(steps)
    tiers = {o["hazard"] for o in hazards.values()}
    top = next((t for t in ("gate", "danger", "caution") if t in tiers), "")
    electric = any(ELECTRIC.search(" ".join(str(o.get(f) or "") for f in ("say", "safety"))) for o in hazards.values()
                   if o["hazard"] in ("gate", "danger") and not CAR_BATTERY.search(" ".join(str(o.get(f) or "") for f in ("title", "say", "safety"))))
    return {"level": top, "electric": electric}


if __name__ == "__main__":  # self-check on the bundled tutorials' real lines
    assert [v["hazard"] for v in annotate({"1": {"say": "Cut power to the circuit.", "safety": "Make sure the circuit is dead."},
                                           "2": {"say": "Remove the two wires from the old switch.", "safety": ""}}).values()] == ["gate", "danger"]
    assert annotate({"1": {"say": "Kill the power at the breaker panel.", "safety": ""}, "2": {"say": "Unscrew the plate."}})["2"]["hazard"] == "danger"
    assert annotate({"1": {"say": "Push the red wire to the battery's positive terminal.", "safety": ""}})["1"]["hazard"] == ""  # a 9 V battery
    assert annotate({"1": {"title": "Turn off entry bedroom power", "say": "Flip the black breaker toggle for the entry and bedroom to the off position.",
                           "safety": "Touch only the plastic breaker handle with dry hands."}})["1"]["hazard"] == "gate"
    assert tier("Switch the breaker OFF and prove dead with the tester before touching anything.") == "gate"
    assert tier("Confirm the breaker is back OFF and the tester is dark before touching the white wires.") == "gate"
    assert tier("Assume every wire is live until the tester shows no power.") == "gate"
    assert tier("Turn off power at the breaker box and confirm it is off before you touch the thermostat.") == "gate"
    assert tier("Keep the breaker off while you press the bare wire ends into the tabs.") == "danger"
    assert tier("Do not let the black clamp touch either red clamp while you clip it on.") == "danger"
    assert tier("Keep fingers clear of the crimp jaws; they pinch hard when you squeeze.") == "caution"
    assert tier("Keep the red and black bare ends from touching once the battery is snapped on.") == "caution"
    assert tier("Anchor the shelf to the wall: it can tip over.") == "danger"  # manual warnings (CR-006)
    assert tier("Two people are needed to lift the frame.") == "danger"
    assert tier("Anchor the rope around the post, then pull the knot snug.") != "danger"
    assert tier("Two people can follow along on one breadboard.") != "danger"
    assert tier("Small parts: keep them away from children.") == "caution"
    assert tier("") == ""
    assert tier("Keep children away. Switch the breaker off and prove it dead with the tester before you touch any wire.") == "gate"
    assert tier("Make sure the power is off at the breaker before you touch any wire.") == "gate"
    assert tier("Ensure the breaker is off before removing the cover.") == "gate"
    assert tier("Check that the circuit is dead before you start.") == "gate"
    print("ok")
