# FollowAlong

**Instructions that see your work: real-time visual guidance for hands-on work.**

People learn and do hands-on work from how-to videos and manuals, and the instructions never know whether you got the
step right: you find out when it doesn't work. FollowAlong turns any video, manual or set of photos into a live, step-by-step guide that
follows along through your camera, checks every step before the next one starts, and tells you the moment something's
off, by voice, hands-free. Muse Spark writes the steps and judges what geometry can't; SAM 3.1 measures what's there.

**Who it's for:** anyone doing hands-on work from instructions: makers and students with electronics and robotics kits,
people doing repairs and installs at home, assembly and workforce training (new hires checked step by step, with a log
of every check), and the teams and creators who want to see exactly where people get stuck.

## Meta Global AI Hackathon 2026 — submission overview

- **Track · problem statement:** Indie Developer · Build for Your World
- **Provenance:** built from scratch during the hackathon window (Oct 26 – Nov 4, 2026) by Samruddhi Kakade and Unnati Shah.
- **Meta models at runtime:**
  - **Muse Spark** (Meta Model API): turns a video or an instruction manual into a step plan, runs the live coach
    (a look every 2–3 s with short-term memory), judges the checks code can't measure, answers spoken questions.
  - **SAM 3.1**: outlines the hand and each step's parts so plain code decides measured checks (present, count, inside).
  - **Muse Voice Transcribe**: hands-free voice commands, questions and barge-in.
- **Not a chatbot, not a one-prompt wrapper:** instructions → step plan → a camera that watches the work → SAM-measured
  checks plus Muse Spark judgements, rule-based safety gates, scored on CF-Bench (303 labelled frames from CC BY
  tutorials). Best case, the breadboard guide: 93% of frames right and no unfinished step let through.
- **Bonus prize targeted:** Best Agentic Use-Case (the coach watches, decides, interrupts and advances on its own,
  with the learner able to overrule it).
- **Quickstart, no key:** `docker compose up` → **Watch a replay**. Live mode: `cp .env.example .env`, set `MODEL_API_KEY`.

Start from a video, a kit manual, or snapped pages. FollowAlong plays one step at a time and watches your work through
the camera: done → the next step plays; wrong → it tells you what's different and loops the three seconds you need to
see again; not sure → it asks you. The same engine works for any hands-on task (a knot, a cable, a thermostat, a robot
arm), and the bundled lessons show that range.

> This repo is a generated public mirror of the team's working repo; it's regenerated on every release, so please open issues here but send no pull requests.

- **Live demo:** **https://unnatibshah.github.io/followalong/** (Chrome or Edge; "Watch a replay" needs no camera)
- **Paper:** [paper/followalong.pdf](paper/followalong.pdf) — *Grounded Step Verification for Learning Physical Skills from Video*
- **Benchmark:** [CF-Bench](#cf-bench-testing-without-a-human-in-the-loop) — 303 auto-labelled counterfactual frames from CC BY tutorials, every model answer recorded

## Try it in 2 minutes (no API key, no camera)

```bash
git clone https://github.com/unnatibshah/followalong.git && cd followalong
docker compose up            # or: ./run.sh  (Python 3.12+)
```

Open http://localhost:8000 → **See it in action**. Each replay runs the real app on a practice session filmed in a
**different** video from the tutorial it follows (another person, other parts, another table): the tutorial teaches,
that footage is "you", and every look and every check in it ran live on those frames (SAM 3.1 + Muse Spark), then was
recorded so it replays without a camera or a key.

**Live mode, hosted:** [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/unnatibshah/followalong)
Render asks for your `MODEL_API_KEY` (kept in Render's secret settings, never in the repo).

**Live mode, locally:** `cp .env.example .env`, set `MODEL_API_KEY` (Meta Model API), `docker compose up`, pick a tutorial,
allow the camera. A check costs about half a cent.

## A live coach, like a teacher next to you

Point your phone (or laptop) camera at your work and just do the step. Every 2–3 seconds while you move, Muse Spark
takes a fresh look, remembers what it saw on the last few looks, and decides: *working*, *mistake*, *can't see*,
*looks done* or *idle*. It stays quiet while you're doing fine, and talks like a person supervising you:

- **"Oh, wait, that's glowing orange. Pull it out and put in the round blue LED with its legs in those same rows."**
  (cuts in immediately, even over its own sentence)
- **"I can't see the breadboard. Move the phone back so I can see the upper left of the board."**
- **"Hold your hands clear for a second so I can see the top of the board."**
- When it thinks you're done, the SAM 3.1 grounded check confirms it, then: **"Yes, that's right! Next up, step 3: …"**

On a phone it's a full-screen camera with the tutorial clip picture-in-picture; "Add to Home Screen" installs it like
an app. ~$0.05 per minute of work.

## Bring any video, start in one tap

- **Paste a Creative Commons YouTube or Vimeo link**, upload a file, or **record yourself** doing the task once so others can learn it.
  Muse Spark turns it into exact steps (rows and columns, terminal labels, polarity, colour order), with a **safety
  line** for every risky step ("Switch the breaker OFF and prove dead with the tester before touching anything").
- **Upload a kit manual** (a PDF up to 40 pages) or **snap the pages** with your phone (up to 20, reorder or drop a bad
  shot): Muse Spark reads every page and builds an animated instruction video from the manual's own figures (each one
  draws itself on and zooms to where the work happens), with its warnings as safety lines, and the coach follows it.
- **One tap** from the tutorial page: camera and voice coach start together (the 18+ confirmation is asked once).
- **Talk to it like an assistant**, and **interrupt it** mid-sentence: it stops and listens.

## Built for real use, not just a demo

- **Safety you can trust.** Every risky step is classified by rules, not by the model (`app/server/safety.py`).
  Steps where you must *make* something safe ("switch the breaker off and prove it dead") **stop the lesson until you
  say "it's safe"**, because a camera can't see electricity. Steps with electricity, car batteries or moving parts
  **never advance on the camera's word alone**: you get the final say. Mains and car lessons open with a plain notice
  of what the camera can and can't do. The coach is told never to claim power is off.
- **"No, it's right."** Disagree with the coach by voice or a tap and you move on. Every disagreement is logged with
  what the check said, as a label for the next round of improvements.
- **Many people, one API key.** A shared limiter keeps every learner under the key's 60 requests/minute: checks and
  questions always get through (they wait briefly if they must); the live coach's looks are the first to be skipped,
  and the server tells each phone to look less often as the key fills up.
- **Picks up where you left off.** A reload, a phone call or a closed tab: the home page shows *Continue · step 4 of 8*.
- **Screen stays on** while your hands are busy (Wake Lock).
- **Framing help.** The phone measures light and focus itself (no upload) and the coach says *"It's a bit dark for
  me"* or *"The picture's blurry"*, calibrated on 156 real frames so it doesn't nag.
- **Your language.** Pick the coach's language (English, Español, Français, Deutsch, Português, Italiano, हिन्दी): it
  speaks, listens and answers in it; the lesson's directions are translated once and cached.
- **Status page** (`#/ops`): model latency p50/p95, errors, rate-limit use, spend, browser errors, and the learner
  funnel (finished, mistakes caught, coach overruled, safety steps confirmed).
- **Privacy controls.** *Delete my data* on the privacy page erases your uploads, edits and session logs at once;
  session logs are deleted after 30 days anyway; lesson progress stays in your browser.
- **Share a lesson:** a link opens it straight away.

## Measured latency (local, real Meta calls)

| What | Time |
|---|---|
| Pasted link → tutorial ready to follow (90–100 s video) | ~1.9 min (directions arrive ~40 s later in the background) |
| Live coach: a mistake on camera → "Oh, wait…" spoken | 1.5–3 s (a look every 1.5 s while you move, 2 in flight) |
| "Looks done" → grounded SAM 3.1 + Muse Spark check | 2–3.5 s |
| A spoken question → the coach's answer | 1.5–2.5 s |

## Hands-free voice coach

Turn on the voice coach (one switch before you start) and talk to it like ChatGPT while your hands stay on the work:

| You say | FollowAlong |
|---|---|
| *"Am I doing it right?"* · *"Is this right?"* · *"How am I doing?"* | Runs the real check (SAM 3.1 outlines + Muse Spark on the matched crop), then answers: *"Yes, that's right! Next up, step 3: …"* or *"Not quite. That LED is glowing orange; you need the round blue one, with its legs in the same rows."* |
| *"Wait, what did you say?"* · *"Say that again"* | Repeats its last sentence (no model call). |
| *"Where exactly does the jumper go?"* · anything else | The coach answers with the whole lesson, the step's **exact directions**, the last check and the conversation so far, looking at your camera and the tutorial's finished frame. |
| *"Next"* · *"Go back"* · *"Go to step three"* · *"Skip this"* · *"Again"* · *"Pause"* · *"Yes"* / *"No"* | Moves through the lesson, replays the step, answers the coach's question. |
| *"It's safe"* · *"The breaker is off"* | Confirms a safety step; the lesson continues. |
| *"No, it's right"* · *"You're wrong"* | Overrules the check and moves on (logged as a label). While the coach is only talking, it runs a proper check instead. |

When it's your turn it reads out the step's exact directions; when a check passes it tells you the next step.
**Exact directions** come from a second Muse Spark pass over the video, its narration and each step's finished frame,
written as placements ("LED long leg (anode, +) → the hole just below the top blue rail, left end"; "red wire → the
Rc push-tab, bottom left"), with breadboard rows/columns, terminal labels, polarity and colour order wherever the video
shows them, and an honest "match the picture" where it doesn't. Speech in: Muse Voice Transcribe. Speech out: your
device's own voice.

## How it works

```
tutorial video ──► Muse Spark (once) ──► step plan: steps + summaries, "done" end frames,
                                          objects, checks typed MEASURED or JUDGED
                                          │
webcam ──► motion (browser) ──► still? ──► SAM 3.1: hands away? ──► SAM 3.1 outlines of the step's objects
                                                                     │
                                  measured checks in code (present, count, inside, adjacent, left of, size)
                                  judged checks by Muse Spark on a crop around the objects, vs the tutorial's crop
                                                                     │
          verdict: done → next step · wrong → hint + loop the 3-second clip · unsure → ask the learner
```

**Measure what you can, judge what you must, ask when unsure.** A measured failure always wins over the model; a
low-confidence judgement asks the learner instead of guessing. That asymmetry is meant to stop a VLM waving a
half-finished step through.

## Results

**CF-Bench v2:** 303 frames from the 9 bundled Creative Commons tutorials (63 steps: breadboard LED, figure-eight
knot, half Windsor, soldering, jump-start, thermostat, light switch, crossover Ethernet cable, robot pick-and-place).
"False advance" = a not-done step the system called **done**, so the learner moves on past a mistake. "Done recall" =
finished steps it recognised (a system that rejects correct work is useless too).

| System | Accuracy | Balanced acc. | Done recall | False advance | Mistakes flagged |
|---|---|---|---|---|---|
| Pixel similarity to the tutorial frame (no model) | 58% | | | 49% | – |
| Muse Spark alone (VLM-as-judge, whole frame) | 64% | 62% | 48% | **24%** | 76% |
| SAM 3.1 measured checks alone | 64% | 68% | 88% | 53% | 47% |
| **FollowAlong verifier (SAM 3.1 + Muse Spark)** | **70%** | **70%** | **68%** | 28% | 72% |
| **FollowAlong product (soft actor gate)** | **70%** | **70%** | **68%** | 28% | 51% + "move your hand" |

What this shows, honestly:

- **Measuring plus judging is the most accurate** (70% vs 64%) and recognises far more finished work (68% vs 48%):
  the VLM alone rejects half of correctly finished steps, which in a lesson means "that's wrong" when it isn't.
- **It does not let fewer mistakes through overall on v2** (28% vs 24%). Most of the gap is one video: the robot
  arm's 16 near-identical "move the cube" steps, where a half-done move looks finished (17 of 48 false advances vs 7).
  Without it, the verifier lets through 25% vs 28% for the VLM alone. Unfinished steps are its weak case (46 vs 55 of
  63 caught): all the parts are present, so the measured checks pass.
- **Where it's strongest:** breadboard 93%, figure-eight knot 100% of frames right, 0 false advances on either.
- **CF-Bench v1** (Oct 28 – Nov 2, 7 standard-licence YouTube tutorials, 211 frames; frames not published, labels and
  every answer in [eval/cfbench-v1/](eval/cfbench-v1/)): the verifier cut false advances from 21% (Muse Spark alone)
  to 12% at equal accuracy (72%), and plan self-calibration raised finished-step recognition from 22% to 51%.

Cost: ~$0.005 per check. Full tables: [eval/cfbench/summary.md](eval/cfbench/summary.md) · paper:
[paper/followalong.pdf](paper/followalong.pdf). **Hands, grippers, tools:** the actor doing the work is named per
video ("hand", "robot gripper", "none") and outlined next to the parts, never waited for, so the same checks run on
robot footage.

## CF-Bench: testing without a human in the loop

Nobody filmed themselves making mistakes. `tools/cfbench.py` makes learner frames *from the tutorial itself*, labelled
by construction: the finished state under a different simulated camera (and in dim light), the step not yet finished,
the key part blurred out, and the wrong-colour part (red↔blue swapped around the step's objects). Every model answer is
kept in `eval/cfbench/recorded-*.jsonl`, so all numbers re-score offline for $0. `tools/make_replays.py` turns the
same answers into the bundled replays.

```bash
cd app
.venv/bin/python tools/cfbench.py make && .venv/bin/python tools/cfbench.py baseline
.venv/bin/python -m server.evaluate --labels eval/cfbench/labels.jsonl --ablation verify --recorded eval/cfbench/recorded-full.jsonl
.venv/bin/python tools/paper_metrics.py
```

## Tutorials (Creative Commons, CC BY)

Every bundled tutorial is a YouTube video its creator published under the **Creative Commons Attribution licence
(CC BY)**, which allows reuse with credit. We checked each licence on YouTube and rejected videos with stock footage
the uploader couldn't relicense. FollowAlong cuts each video into steps, end frames and short clips; the videos are not
otherwise changed. Credit, as CC BY asks: title, creator, link, licence (CC BY 3.0, as YouTube offers it).

| Tutorial in the app | Original video | Creator | Link |
|---|---|---|---|
| Light an LED on a breadboard | “LED with Battery and resistor \| Electronic Project” | Minute Electronics | [youtube.com/watch?v=2N5oN4P547M](https://www.youtube.com/watch?v=2N5oN4P547M) |
| Tie a figure-eight knot | “Amazing Figure 8 Knot \| Simple to Tie and Easily Undone” | Great Knot | [youtube.com/watch?v=oG2RaR3fqVo](https://www.youtube.com/watch?v=oG2RaR3fqVo) |
| Tie a half Windsor knot | “How To Tie a Half Windsor Knot” | Massive Guides | [youtube.com/watch?v=ksitSrCO64c](https://www.youtube.com/watch?v=ksitSrCO64c) |
| Solder two wires together | “How To Solder 2 Wires Together \|\| Soldering Wires Together” | Mr SolderFix | [youtube.com/watch?v=MWmc6Yges-I](https://www.youtube.com/watch?v=MWmc6Yges-I) |
| Jump-start a car | “how to jump start a car” | Tremolo Den | [youtube.com/watch?v=uadUt_Ag2jM](https://www.youtube.com/watch?v=uadUt_Ag2jM) |
| Install a wall thermostat | “HOW TO install A single pole Wall mount THERMOSTAT” | Bahiano Reno | [youtube.com/watch?v=lFDfoIbFaJY](https://www.youtube.com/watch?v=lFDfoIbFaJY) |
| Install a smart light switch | “HOW TO WIRE A LIGHT SWITCH OR REPLACE A LIGHT SWITCH - SINGLE POLE - CASETA SMART SWITCH” | Ultimate Tech Hub | [youtube.com/watch?v=-tjN6mRzxeI](https://www.youtube.com/watch?v=-tjN6mRzxeI) |
| Make a crossover Ethernet cable | “CAT6 CROSSOVER CABLE - HOW TO - QUICK & EASY!” | Ultimate Tech Hub | [youtube.com/watch?v=VZZq7MbvAuE](https://www.youtube.com/watch?v=VZZq7MbvAuE) |
| Robot arm: pick and place blocks | “Dobot Magician Lite Pick & Place - Suction Cup Vs Gripper #robot #robotics #technology” | Amatya Katyayan | [youtube.com/watch?v=tfN4FUlOfzg](https://www.youtube.com/watch?v=tfN4FUlOfzg) |

**Practice footage from different videos.** The replay's "you" camera is a different CC BY video from the tutorial it follows, so nothing is checked against its own source: *Tie a figure-eight knot* uses [“Activity at Home: Tie a Figure 8 Knot”](https://www.youtube.com/watch?v=Sq3Ndf_jm_Y) by Terrapin Adventures (a pink cord on a wooden table). Every look and check in it ran live on those frames.

Links pasted into the app are accepted only when the video is Creative Commons licensed; uploads must be videos you
have the rights to. The first version of this project used standard-licence YouTube videos; they are not in this
repository.

## Limits

- Works best with a camera looking down at a desk-sized work area.
- Checks what the camera can see. It asks about what it can't (for example, whether the breaker is off).
- Adults only (18+). Camera frames are sent to Meta's models to check each step and are not stored by us.
  [Privacy notice](web/legal/privacy.md) · [Terms](web/legal/terms.md).

## Licences

- Code: MIT ([LICENSE](LICENSE)). Tutorial videos: their creators' (see above).
- `meta-sam-parser` is under Meta's SAM License; a copy is included with it.

## Team

Samruddhi Kakade · Unnati Shah
