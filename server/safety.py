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
    r"confirm\w*\s+(\w+\s+){0,5}(off|dead)|shows?\s+no\s+power|unplug\w*|disconnect\w*\s+the\s+(power|battery))\b", re.I)
DANGER = re.compile(
    r"\b(breaker|live|mains|volt\w*|electric\w*|shock|tester|power|wires?\s+(are|is)\s+live|clamps?|jump\w*|"
    r"crank\w*|spinning|fans?|belts?|moving\s+parts|flame|gas|fuel|ladder|"
    # manual warnings (CR-006): furniture that can fall, and two-person lifts (not "anchor the rope", "two people can")
    r"tip\w*[\s-]+over|topple\w*|(anchor|fix|attach|secure|mount)\w*\s+(it\s+|this\s+|the\s+\w+\s+)?(to|in|into)\s+"
    r"the\s+(wall|studs?)|wall\s+anchors?|(needs?|requires?|takes?|with)\s+two\s+(people|persons|adults)|"
    r"two\s+(people|persons|adults)\s+(are\s+)?(needed|required))\b", re.I)

ELECTRIC = re.compile(r"\b(breaker|live|mains|volt\w*|electric\w*|shock|tester|power|wires?)\b", re.I)


def tier(safety: str) -> str:
    text = (safety or "").strip()
    if not text:
        return ""
    if GATE.search(text) and not re.match(r"keep\b", text, re.I):
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
        say = v.get("say", "") or ""
        if t != "gate" and GATE.search(say) and ELECTRIC.search(say + " " + (v.get("safety") or "")):
            t = "gate"
        out[k] = {**v, "hazard": t}
    if any(o["hazard"] == "gate" and ELECTRIC.search((o.get("say") or "") + " " + (o.get("safety") or "")) for o in out.values()):
        for o in out.values():
            if o["hazard"] in ("", "caution"):
                o["hazard"] = "danger"
    return out


def lesson(steps: dict) -> dict:
    """The lesson's notice: its highest tier and whether it involves electricity."""
    tiers = {tier(v.get("safety", "")) for v in steps.values() if isinstance(v, dict)}
    top = next((t for t in ("gate", "danger", "caution") if t in tiers), "")
    electric = any(ELECTRIC.search(v.get("safety", "")) for v in steps.values() if isinstance(v, dict)
                   and tier(v.get("safety", "")) in ("gate", "danger"))
    return {"level": top, "electric": electric}


if __name__ == "__main__":  # self-check on the bundled tutorials' real lines
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
    print("ok")
