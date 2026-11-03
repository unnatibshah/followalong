"""Tutorial processing (TD §4.5, prompt spec P1): upload checks → MP4 → Muse Spark step plan → end frames → SAM 3.1
reference outlines. Used by the upload route (providers.Tutorials, as a background task) and by the CLI below.

    python -m server.tutorials build <video> --id thermostat-wiring --bundle [--title T] [--dry-run]
    python -m server.tutorials build <video> --id my-test [--out DIR]     # not bundled: $FA_DATA_DIR/builds/<id>/
    python -m server.tutorials build <video> --id my-test --transcript    # narrated: the speech goes as text too (FR-14)

`--bundle` writes server/fixtures/plans/<id>.json, data/tutorials/<id>/{video.mp4, frames/step-<n>.jpg, refs/} and
the tutorials.json entry. `--dry-run` uploads the MP4 to the Files API (free), counts the request's input tokens,
prints the request and the estimated cost, deletes the file and stops.
"""

import argparse
import asyncio
import hashlib
import io
import json
import logging
import re
import shutil
import sys
import time
import wave
from pathlib import Path

from . import sam
from .checks import engine
from .checks.engine import terminal_label
from .checks.geometry import outline_svg
from .config import CHECKS, DATA, MODELS, PLAN_PROMPT, PLAN_SCHEMA, PRICES, RUNTIME, TIMEOUTS, UPLOAD
from .gateway import CapReached, GatewayError, gateway

MEASURED = ("present", "count", "inside", "adjacent", "left_of", "above", "area_ratio")
NEEDS_B = ("inside", "adjacent", "left_of", "above", "area_ratio")
SIDES = ("left", "right", "above", "below")
SIZE_WORDS = {"small", "large", "big", "medium", "tiny", "long", "short"}
CLIP_S = 3.0
VIDEO_TOKENS_PER_S = 260  # measured Oct 27: a 55 s 720p clip billed 15,092 input tokens; the pre-count said 935
FRAME_BEFORE_END_S = 0.1
TRANSCRIBE_CHUNK_S = 300  # narration sent in pieces of at most 5 min (the file endpoint's limit is 10 min / 32 MB)
TERMINALS = CHECKS["terminals"]
log = logging.getLogger("followalong.tutorials")


class Rejected(Exception):
    """An upload refused before any model call: code `invalid_upload` or `too_long`, with the message to show."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class PlanInvalid(Exception):
    def __init__(self, errors: list[str], empty: bool = False):
        super().__init__("; ".join(errors))
        self.errors, self.empty = errors, empty


class Failed(Exception):
    """Processing failed (`processing_failed`); the message is safe to show."""


# ---- ffmpeg ----

def ffmpeg() -> str:
    """System ffmpeg (the Docker image) or imageio-ffmpeg's bundled one (local runs)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


async def _run(args: list[str], timeout: float) -> tuple[int, str]:
    """Run ffmpeg; returns (exit code, stderr). Killed on timeout or when the job is cancelled."""
    proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:  # timeout or cancel: never leave ffmpeg running
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, err.decode("utf-8", "replace")


def parse_probe(stderr: str) -> dict:
    """`ffmpeg -i` output → {"format": "mp4" | "mov" | "webm" | None, "duration", "video" (codec or None), "audio"}.
    ponytail: parses ffmpeg's banner because imageio-ffmpeg ships no ffprobe; same facts, one binary."""
    fmt = None
    m = re.search(r"^Input #0, ([^,\n]+(?:,[^,\n]+)*?), from", stderr, re.M)
    if m:
        demux = m[1]
        brand = re.search(r"major_brand\s*:\s*(\S+)", stderr)
        if "mp4" in demux.split(","):
            fmt = "mov" if brand and brand[1] == "qt" else "mp4"
        elif "webm" in demux.split(","):
            fmt = "webm"
    d = re.search(r"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)", stderr)
    video = re.search(r"Stream #0:\d+.*?: Video: (\w+)", stderr)
    audio = re.search(r"Stream #0:\d+.*?: Audio: (\w+)", stderr)
    return {"format": fmt, "duration": int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3]) if d else None,
            "video": video[1] if video else None, "audio": audio[1] if audio else None}


async def probe(path: Path) -> dict:
    _, err = await _run([ffmpeg(), "-hide_banner", "-i", str(path)], TIMEOUTS["ffmpeg_s"] / 10)
    return parse_probe(err)


def mmss(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def check_upload(info: dict, max_s: float, convert: bool) -> None:
    """NFR-52, FR-04: a readable video by its content (never the extension), MP4 (MOV/WebM when converting), in length."""
    ok = info["format"] == "mp4" or (convert and info["format"] in ("mov", "webm"))
    if not ok or not info["video"] or not info["duration"]:
        raise Rejected("invalid_upload", "This file isn't a readable MP4 video." if not convert else
                       "This file isn't a readable MP4, MOV or WebM video.")
    if info["duration"] > max_s + 0.5:  # exactly at the limit is accepted (UT-50)
        hosted = max_s < 600
        raise Rejected("too_long", f"This video is {mmss(info['duration'])}. The limit is {mmss(max_s)} "
                       + ("on this demo (10 minutes when you run FollowAlong yourself)." if hosted else "here."))


def convert_args(src: Path, dst: Path, info: dict) -> list[str]:
    """MP4 with H.264 is remuxed (no quality loss); anything else is converted to H.264/AAC. Always faststart."""
    remux = info["format"] == "mp4" and info["video"] == "h264" and info["audio"] in (None, "aac")
    codecs = ["-c", "copy"] if remux else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k"]
    return [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
            *codecs, "-movflags", "+faststart", str(dst)]


def frame_args(video: Path, t: float, out: Path) -> list[str]:
    return [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
            "-frames:v", "1", "-q:v", "2", str(out)]


async def to_mp4(src: Path, dst: Path, info: dict) -> None:
    rc, err = await _run(convert_args(src, dst, info), TIMEOUTS["ffmpeg_s"])
    if rc != 0 or not dst.exists():
        raise Failed("We couldn't read this video. Try exporting it again as MP4.") from RuntimeError(err[-300:])


# ---- the step plan ----

def plan_request(file_id: str, duration: float, structured: bool = True, errors: list[str] | None = None,
                 transcript: str | None = None) -> dict:
    text = PLAN_PROMPT["user"].replace("{duration}", f"{duration:.1f}")
    content = [{"type": "input_text", "text": text}, {"type": "input_file", "file_id": file_id}]
    if transcript:  # FR-14: the narration as text, after the video (prompt spec P1)
        content.append({"type": "input_text", "text": PLAN_PROMPT["transcript"].replace("{transcript}", transcript)})
    if not structured:
        content.append({"type": "input_text", "text": "Answer with only one JSON object, no prose or code fences, "
                        "matching this JSON Schema:\n" + json.dumps(PLAN_SCHEMA)})
    if errors:
        content.append({"type": "input_text", "text": "Your previous answer was rejected for these reasons:\n- "
                        + "\n- ".join(errors[:20]) + "\nReturn the whole step plan again with these fixed."})
    body = {"model": MODELS["plan"], "instructions": PLAN_PROMPT["system"],
            "input": [{"type": "message", "role": "user", "content": content}],
            "max_output_tokens": MODELS["plan_max_output_tokens"]}
    if structured:
        body["text"] = {"format": {"type": "json_schema", "name": "step_plan", "schema": PLAN_SCHEMA, "strict": True}}
    return body


def _json_text(text: str) -> dict:
    text = text.strip()
    if not text.startswith("{"):  # prompted JSON can come wrapped in prose or a fence
        a, b = text.find("{"), text.rfind("}")
        text = text[a:b + 1] if a >= 0 < b else text
    return json.loads(text)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def clean_prompt(name: str, prompt, size, where: str, errors: list) -> tuple[str, str | None]:
    """One SAM concept (prompt spec §2): no commas or lists; size words move to `size` (sizes are filtered in code)."""
    if not _str(prompt):
        errors.append(f"{where}: sam_prompt is empty")
        return "", size
    p = " ".join(prompt.strip().split())
    if re.search(r"[,;/&+]|\b(and|or)\b", p, re.I):
        errors.append(f"{where}: sam_prompt {p!r} must be one concept, no commas or lists")
        return p, size
    words = p.split()
    sizes = [w for w in words if w.lower() in SIZE_WORDS]
    words = [w for w in words if w.lower() not in SIZE_WORDS]
    if sizes and not size:
        size = sizes[0].lower()
    if not words or len(words) > 4 or not re.fullmatch(r"[A-Za-z0-9 '-]+", " ".join(words)):
        errors.append(f"{where}: sam_prompt {p!r} must be a short colour + noun (or label + noun)")
    return " ".join(words), size


def clean_plan(raw, duration: float) -> list[dict]:
    """Validate (FR-11, FR-12, prompt spec) and clean (TD §4.5 step 4) the model's plan. Returns steps with the code
    fields added (ids, end_frame, check ids, needs_review, ratio); model-sent ids or paths are ignored (UT-45).
    Raises PlanInvalid with every problem found."""
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        raise PlanInvalid(["the answer must be an object with a steps list"])
    if not raw["steps"]:
        raise PlanInvalid(["no steps: not a hands-on tutorial"], empty=True)
    errors, steps = [], []
    for i, s in enumerate(raw["steps"], 1):
        w = f"step {i}"
        if not isinstance(s, dict):
            errors.append(f"{w} is not an object")
            continue
        for f in ("instruction", "done_looks_like"):
            if not _str(s.get(f)):
                errors.append(f"{w}: {f} is missing")
        for f in ("start", "end"):
            if not _num(s.get(f)):
                errors.append(f"{w}: {f} must be a number of seconds")
        objects, names = [], set()
        for o in s.get("objects") if isinstance(s.get("objects"), list) else []:
            if not isinstance(o, dict) or not _str(o.get("name")):
                errors.append(f"{w}: an object has no name")
                continue
            name = " ".join(o["name"].split())
            if name.lower() in names:
                errors.append(f"{w}: object name {name!r} is used twice")
            names.add(name.lower())
            prompt, size = clean_prompt(name, o.get("sam_prompt"), o.get("size") if _str(o.get("size")) else None,
                                        f"{w} object {name!r}", errors)
            objects.append({"name": name, "sam_prompt": prompt, "size": size})
        by_name = {o["name"].lower(): o["name"] for o in objects}
        checks = []
        for c in s.get("checks") if isinstance(s.get("checks"), list) else []:
            if not isinstance(c, dict):
                errors.append(f"{w}: a check is not an object")
                continue
            kind, typ, cw = c.get("kind"), c.get("type"), f"{w} check {len(checks) + 1}"
            if kind == "judged" or typ == "judged":
                if kind != "judged" or typ != "judged":
                    errors.append(f"{cw}: a judged check needs kind and type both 'judged'")
                a = b = n = side = None
            elif kind == "measured":
                if typ not in MEASURED:
                    errors.append(f"{cw}: type {typ!r} isn't allowed (use one of {', '.join(MEASURED)} or a judged check)")
                a, b = (by_name.get(str(c.get(k) or "").strip().lower()) for k in ("a", "b"))
                n, side = c.get("n"), c.get("side")
                if a is None:
                    errors.append(f"{cw}: a={c.get('a')!r} isn't one of the step's objects")
                if typ in NEEDS_B and b is None:
                    errors.append(f"{cw}: b={c.get('b')!r} isn't one of the step's objects")
                if typ == "count" and not (isinstance(n, int) and not isinstance(n, bool) and n >= 1):
                    errors.append(f"{cw}: count needs n, a whole number from 1")
                if typ == "adjacent" and side not in SIDES:
                    errors.append(f"{cw}: adjacent needs side left, right, above or below")
                b = b if typ in NEEDS_B else None
                n = n if typ == "count" else None
                side = side if typ == "adjacent" else None
            else:
                errors.append(f"{cw}: kind must be measured or judged")
                continue
            if not _str(c.get("text")):
                errors.append(f"{cw}: text is missing")
            conf = min(1.0, max(0.0, float(c["confidence"]))) if _num(c.get("confidence")) else 0.0
            checks.append({"kind": kind, "type": "judged" if kind == "judged" else typ, "a": a, "b": b, "n": n,
                           "side": side, "ratio": None, "text": str(c.get("text") or "").strip().rstrip("."),  # as the curated plans
                           "confidence": round(conf, 3), "needs_review": conf < CHECKS["check_review_threshold"],
                           "enabled": True})
        mistakes = [m.strip() for m in s.get("common_mistakes") or [] if _str(m)][:3]
        steps.append({"start": s.get("start"), "end": s.get("end"), "done_at": s.get("done_at"), "instruction": str(s.get("instruction") or "").strip(),
                      "done_looks_like": str(s.get("done_looks_like") or "").strip(), "objects": objects,
                      "checks": checks, "common_mistakes": mistakes,
                      "key_clip_start": s.get("key_clip_start"), "key_clip_end": s.get("key_clip_end"),
                      "hands_ok": s.get("hands_ok") is True})
    if errors:
        raise PlanInvalid(errors)
    return clean_times(steps, duration)


def clean_times(steps: list[dict], duration: float) -> list[dict]:
    """Clamp to the video, order by start, make contiguous and non-overlapping, key clip exactly 3 s inside the step
    (the model's if it starts inside, else the last 3 s; the whole step if shorter), then assign ids."""
    for s in steps:
        s["start"], s["end"] = (round(min(max(float(s[k]), 0.0), duration), 2) for k in ("start", "end"))
    steps.sort(key=lambda s: (s["start"], s["end"]))
    errors = []
    for prev, s in zip(steps, steps[1:]):
        s["start"] = prev["end"]  # overlap trimmed, gap given to the step it leads into
    for i, s in enumerate(steps, 1):
        if s["end"] - s["start"] < 0.5:
            errors.append(f"step {i} ({s['instruction'][:40]!r}) has no time of its own after removing overlaps")
    if errors:
        raise PlanInvalid(errors)
    for i, s in enumerate(steps, 1):
        a, b = s["start"], s["end"]
        if b - a <= CLIP_S:
            ks, ke = a, b
        else:
            k = s.get("key_clip_start")
            ks = min(float(k), b - CLIP_S) if _num(k) and a <= k < b else b - CLIP_S
            ke = ks + CLIP_S
        d = s.get("done_at")  # the end frame's second: the model's if inside the step, else just before the end
        done = round(d, 2) if _num(d) and a < d <= b - FRAME_BEFORE_END_S else round(b - FRAME_BEFORE_END_S, 2)
        s.update(id=i, done_at=done, end_frame=f"frames/step-{i}.jpg", key_clip_start=round(ks, 2), key_clip_end=round(ke, 2))
        for j, c in enumerate(s["checks"], 1):
            c["id"] = f"s{i}c{j}"
    order = ("id", "start", "end", "done_at", "instruction", "done_looks_like", "end_frame", "objects", "checks",
             "common_mistakes", "key_clip_start", "key_clip_end", "hands_ok")
    return [{k: s[k] for k in order} for s in steps]


def needs(steps: list[dict]) -> list[str]:
    """FR-34: the "you'll need" list from the objects (unique names in order of use, with counts). A terminal is part
    of the piece it's on, so it isn't listed."""
    out, counts = {}, {}
    for s in steps:
        for o in s["objects"]:
            if not terminal_label(o["name"]):
                out.setdefault(o["name"].lower(), o["name"])
        for c in s["checks"]:
            if c["type"] == "count" and c["a"] and c["n"] and c["n"] > 1:
                counts[c["a"].lower()] = max(counts.get(c["a"].lower(), 0), c["n"])
    return [f"{n} ×{counts[k]}" if k in counts else n for k, n in out.items()]


def build_plan(tid: str, steps: list[dict], transcript_used: bool = False) -> dict:
    plan = {"tutorial_id": tid, "model": MODELS["plan"], "transcript_used": transcript_used,
            "prompt_version": PLAN_PROMPT["version"], "plan_hash": "", "reviewed": False, "needs": needs(steps),
            "steps": steps}
    return rehash(plan)


def rehash(plan: dict) -> dict:
    body = json.dumps({**plan, "plan_hash": ""}, sort_keys=True).encode()
    plan["plan_hash"] = "sha256:" + hashlib.sha256(body).hexdigest()
    return plan


# ---- the narration transcript (FR-14) ----

def wav_chunks(wav: bytes, max_s: float) -> list[tuple[float, bytes, float]]:
    """Split a WAV into pieces of at most `max_s` seconds: [(start second, WAV bytes, seconds)]."""
    with wave.open(io.BytesIO(wav)) as w:
        params, rate, n = w.getparams(), w.getframerate(), w.getnframes()
        frames = w.readframes(n)
    step, width = int(max_s * rate), params.sampwidth * params.nchannels
    out = []
    for i in range(0, n, step):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as c:
            c.setparams(params)
            c.writeframes(frames[i * width:(i + step) * width])
        out.append((i / rate, buf.getvalue(), min(step, n - i) / rate))
    return out


def transcript_lines(data: dict, offset: float) -> list[str]:
    """`[12.3] words` per turn (ENDPOINTING), or the whole transcript at the chunk's start."""
    turns = [t for t in data.get("turns") or [] if (t.get("transcript") or "").strip()]
    if turns:
        return [f"[{offset + t.get('startMs', 0) / 1000:.1f}] {' '.join(t['transcript'].split())}" for t in turns]
    text = " ".join((data.get("transcript") or "").split())
    return [f"[{offset:.1f}] {text}"] if text else []


async def narration(video: Path, stats: dict) -> str | None:
    """The tutorial's speech as timed lines, from Muse Voice Transcribe (file mode, ENDPOINTING), in chunks of
    TRANSCRIBE_CHUNK_S (the file endpoint takes 10 min / 32 MB). None when the video has no audio or no speech, or the
    transcription fails (the plan is then made from the video alone). Never logged or stored."""
    wav = video.with_suffix(".narration.wav")
    rc, _ = await _run(audio_args(video, wav), TIMEOUTS["ffmpeg_s"])
    try:
        if rc != 0 or not wav.exists():
            return None
        data = wav.read_bytes()
    finally:
        wav.unlink(missing_ok=True)
    lines = []
    try:
        for start, chunk, seconds in wav_chunks(data, TRANSCRIBE_CHUNK_S):
            res, usd = await gateway.transcribe(chunk, seconds, None, "ENDPOINTING", TIMEOUTS["narration_s"])
            stats["transcript_cost_usd"] = round(stats.get("transcript_cost_usd", 0) + usd, 6)
            lines += transcript_lines(res, start)
    except CapReached:
        raise
    except GatewayError as e:
        stats["transcript_error"] = e.code
        log.warning(json.dumps({"event": "transcript_failed", "error": e.code, "status": e.status}))
        return None
    stats["transcript_lines"] = len(lines)
    return "\n".join(lines) or None


def audio_args(video: Path, out: Path) -> list[str]:
    """Mono 16-bit PCM WAV at 16 kHz (Muse Voice Transcribe's file format; 10 min is 19 MB, under its 32 MB)."""
    return [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", "-map_metadata", "-1", str(out)]


async def find_steps(file_id: str, duration: float, stats: dict, transcript: str | None = None) -> list[dict]:
    """Muse Spark watches the video (FR-10): structured output first, prompted JSON if that's refused with a video
    (a day-1 question); one retry with the validation errors (FR-13). Raises Failed."""
    errors, structured = None, True
    for attempt in range(2):
        try:
            data, usd = await gateway.responses(plan_request(file_id, duration, structured, errors, transcript),
                                                TIMEOUTS["plan_s"])
        except GatewayError as e:
            if isinstance(e, CapReached):
                raise
            if structured and e.status == 400:  # text.format not accepted with video: ask for JSON instead
                structured = False
                stats["structured_output"] = False
                data, usd = await gateway.responses(plan_request(file_id, duration, False, errors, transcript),
                                                    TIMEOUTS["plan_s"])
            else:
                raise Failed("Muse Spark didn't answer. Try again in a minute.") from e
        stats.setdefault("structured_output", structured)
        u = data.get("usage") or {}
        stats["cost_usd"] = round(stats.get("cost_usd", 0) + usd, 6)
        stats["calls"] = stats.get("calls", 0) + 1
        stats.update(input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                     reasoning_tokens=(u.get("output_tokens_details") or {}).get("reasoning_tokens"),
                     video_frames_processed=u.get("video_frames_processed"))
        try:
            return clean_plan(_json_text(sam.output_text(data)), duration)
        except PlanInvalid as e:
            if e.empty:
                raise Failed("We couldn't find hands-on steps in this video.") from e
            errors = e.errors
        except (ValueError, TypeError) as e:
            errors = [f"the answer wasn't valid JSON ({e})"]
        stats.setdefault("invalid", []).append(errors)
    raise Failed("We couldn't turn this video into steps. Try a shorter or clearer video.")


# ---- end frames and reference outlines ----

async def end_frames(video: Path, steps: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for s in steps:
        jpg = out / f"step-{s['id']}.jpg"
        for back in (0.0, 0.5, 1.0):  # a step that ends with the video can be past its last frame
            rc, err = await _run(frame_args(video, s["done_at"] - back, jpg), TIMEOUTS["ffmpeg_s"] / 10)
            if rc == 0 and jpg.exists():
                break
        else:
            raise Failed("We couldn't read frames from this video.") from RuntimeError(err[-300:])


async def references(step: dict, frame: bytes) -> dict:
    """FR-15: SAM 3.1 on the end frame, once per distinct prompt (every "<label> terminal" shares the terminal prompt,
    mapped left to right as in the live check). ReferenceOutlines (data dictionary) plus the cost."""
    prompt_of = {o["name"]: TERMINALS["prompt"] if terminal_label(o["name"]) else o["sam_prompt"] for o in step["objects"]}
    prompts = list(dict.fromkeys(prompt_of.values()))
    segs = dict(zip(prompts, await asyncio.gather(*(sam.segment(frame, p) for p in prompts), return_exceptions=True)))
    for p, seg in segs.items():
        if isinstance(seg, CapReached):
            raise seg
    size = next((s["size"] for s in segs.values() if not isinstance(s, Exception) and s["size"]), None)
    shape = (size[1], size[0]) if size else (720, 1280)
    ok = {p: s for p, s in segs.items() if not isinstance(s, Exception)}
    sized = engine.sizes([o for o in step["objects"] if prompt_of[o["name"]] in ok], prompt_of, ok, shape)  # FR-44
    objects = []
    for name, p in prompt_of.items():
        seg = segs[p]
        if isinstance(seg, Exception):
            objects.append({"name": name, "found": False, "error": "sam_failed"})
            continue
        label = terminal_label(name)
        insts = sorted(engine.terminal_screws(seg["instances"], shape) if label else sized[name],
                       key=lambda i: (i["box"][0] + i["box"][2]) / 2)
        if label:
            inst = insts[TERMINALS["order"].index(label)] if len(insts) == len(TERMINALS["order"]) else None
        else:
            inst = max(insts, key=lambda i: i["score"] or 0, default=None)
        if inst is None:
            objects.append({"name": name, "found": False})
            continue
        x1, y1, x2, y2 = inst["box"]
        objects.append({"name": name, "found": True, "box": inst["box"], "score": inst["score"],
                        "area_px": int(inst["mask"].sum()), "aspect": round((x2 - x1 + 1) / max(1, y2 - y1 + 1), 3),
                        "count": len(insts), "outline_svg": outline_svg(inst["mask"])})
    return {"step_id": step["id"], "frame_size": list(size) if size else None, "objects": objects,
            "cost_usd": round(sum(s["cost_usd"] for s in segs.values() if not isinstance(s, Exception)), 6)}


def apply_refs(step: dict, refs: dict) -> None:
    """area_ratio's reference `ratio` comes from the end frame's outlines; without both outlines, it needs review."""
    area = {o["name"]: o.get("area_px") for o in refs["objects"] if o.get("found")}
    for c in step["checks"]:
        if c["type"] == "area_ratio":
            if area.get(c["a"]) and area.get(c["b"]):
                c["ratio"] = round(area[c["a"]] / area[c["b"]], 4)
            else:
                c["needs_review"] = True


# ---- the pipeline ----

async def process(src: Path, out: Path, tid: str, info: dict, progress=lambda text: None,
                  transcript: bool | None = None) -> tuple[dict, dict]:
    """src (validated) → out/video.mp4, out/frames/, out/refs/, and the plan (not saved: the caller decides where).
    `transcript` (default: models.toml `plan_transcript`) also sends the narration as text (FR-14).
    Returns (plan, stats with per-stage latencies). Raises Failed or CapReached; cancelling stops ffmpeg and leaves
    an in-flight Muse Spark call to finish (so its cost is counted) and delete its file."""
    stats, stages = {}, {}
    use_transcript = MODELS["plan_transcript"] if transcript is None else transcript
    out.mkdir(parents=True, exist_ok=True)
    video = out / "video.mp4"
    progress("finding_steps 0/0")
    t = time.monotonic()
    await to_mp4(src, video, info)
    stages["convert"] = round((time.monotonic() - t) * 1000)
    probed = await probe(video)
    duration = probed["duration"] or info["duration"]
    t = time.monotonic()
    text = await narration(video, stats) if use_transcript and probed["audio"] else None
    if use_transcript:
        stages["transcribe"] = round((time.monotonic() - t) * 1000)

    uploaded = {}

    async def ask() -> list[dict]:
        t0 = time.monotonic()
        try:
            uploaded["id"] = await gateway.upload_file(video, int(UPLOAD["ttl_h"] * 3600))
        except GatewayError as e:
            raise Failed("We couldn't send the video to Muse Spark. Try again in a minute.") from e
        stages["files_upload"] = round((time.monotonic() - t0) * 1000)
        t1 = time.monotonic()
        try:
            return await find_steps(uploaded["id"], duration, stats, text)
        finally:
            stages["muse_spark"] = round((time.monotonic() - t1) * 1000)

    try:  # the uploaded video is reused by the directions pass, which deletes it when done (FR-08)
        job = asyncio.ensure_future(ask())
        job.add_done_callback(lambda f: f.cancelled() or f.exception())  # a cancelled upload's late failure isn't news
        steps = await asyncio.shield(job)
        return await _after_plan(tid, steps, video, out, duration, text, stats, stages, progress, uploaded)
    except BaseException:
        if uploaded.get("id") and not uploaded.get("handed_over"):
            await gateway.delete_file(uploaded["id"])
        raise


_background: set = set()  # directions passes still running after their tutorial is ready (kept referenced)


async def _after_plan(tid, steps, video, out, duration, text, stats, stages, progress, uploaded, frames_ready=False):
    """End frames, then SAM outlines + self-calibration, with the exact-directions pass alongside. The tutorial is
    ready once calibrated; the directions land in details.json when they arrive (the coach uses the plain
    instruction until then). `frames_ready`: out/frames/ already holds the end frames (a manual's figures)."""
    file_id = uploaded.get("id")
    n = len(steps)
    progress(f"outlining 0/{n}")
    if not frames_ready:
        t = time.monotonic()
        await end_frames(video, steps, out / "frames")
        stages["end_frames"] = round((time.monotonic() - t) * 1000)
    from . import details
    t_details = time.monotonic()

    async def directions():  # exact spoken directions for the voice coach; a failure only means plainer directions
        try:
            if not MODELS.get("step_details", True):
                return None
            return await details.build(build_plan(tid, steps, transcript_used=bool(text)), out, duration, text, file_id)
        except CapReached:
            raise
        except Exception as e:  # noqa: BLE001 optional enrichment: never fails an upload whose plan is ready
            log.warning(json.dumps({"event": "details_failed", "error": getattr(e, "code", type(e).__name__)}))
            return None
        finally:
            stages["details"] = round((time.monotonic() - t_details) * 1000)

    async def directions_then_clean_up():
        try:
            det = await directions()
            if det:
                (out / "details.json").write_text(json.dumps(det, ensure_ascii=False), encoding="utf-8")
                stats["details_cost_usd"] = det["cost_usd"]
        finally:
            if file_id:
                await gateway.delete_file(file_id)
    details_job = asyncio.ensure_future(directions_then_clean_up())
    uploaded["handed_over"] = True
    _background.add(details_job)
    details_job.add_done_callback(lambda f: (_background.discard(f), f.cancelled() or f.exception()))

    t, done = time.monotonic(), 0
    (out / "refs").mkdir(exist_ok=True)

    async def one(s):
        nonlocal done
        refs = await references(s, (out / "frames" / f"step-{s['id']}.jpg").read_bytes())
        apply_refs(s, refs)
        (out / "refs" / f"step-{s['id']}.json").write_text(json.dumps(refs), encoding="utf-8")
        done += 1
        progress(f"outlining {done}/{n}")
        return refs
    # One step at a time, its prompts in parallel (DEF-017: ~22 requests at once all timed out on a 4-minute video).
    # ponytail: sequential steps; a semaphore over all prompts if processing time matters more than SAM's rate limits
    refs = [await one(s) for s in steps]
    stats["sam_cost_usd"] = round(sum(r["cost_usd"] for r in refs), 6)
    objs = [o for r in refs for o in r["objects"]]
    stats["refs_failed"] = sum(o.get("error") == "sam_failed" for o in objs)
    if objs and stats["refs_failed"] == len(objs):  # nothing outlined: don't call the tutorial ready (FR-15)
        raise Failed("We couldn't outline the objects in this video. Try again in a minute.")
    stages["sam"] = round((time.monotonic() - t) * 1000)
    # Reference self-calibration: measured checks that fail on the tutorial's own end frames are switched off
    from .calibrate import calibrate
    t = time.monotonic()
    plan, notes = await calibrate(build_plan(tid, steps, transcript_used=bool(text)), out / "frames")
    stages["calibrate"] = round((time.monotonic() - t) * 1000)
    stats["calibration_disabled"] = sum(len(n.get("disabled", [])) for n in notes)
    if not MODELS.get("step_details", True):
        await details_job  # nothing to wait for; keeps tests deterministic
    stats["stages_ms"] = stages
    stats["total_cost_usd"] = round(stats.get("cost_usd", 0) + stats["sam_cost_usd"], 6)
    return plan, stats


# ---- CLI ----

def summary(plan: dict) -> str:
    lines = [f"{plan['tutorial_id']}: {len(plan['steps'])} steps · model {plan['model']} · {plan['prompt_version']}",
             "You'll need: " + ", ".join(plan["needs"])]
    for s in plan["steps"]:
        lines.append(f"\n{s['id']}. [{mmss(s['start'])}–{mmss(s['end'])}] {s['instruction']}  "
                     f"(key clip {s['key_clip_start']}–{s['key_clip_end']} s)")
        lines.append(f"   done looks like: {s['done_looks_like']}")
        lines.append("   objects: " + (", ".join(f"{o['name']} → {o['sam_prompt']!r}" + (f" ({o['size']})" if o["size"] else "")
                                         for o in s["objects"]) or "none"))
        for c in s["checks"]:
            args = ", ".join(str(x) for x in (c.get(k) for k in ("a", "b", "n", "side", "ratio")) if x is not None)
            flag = "  ⚠ needs your eyes" if c.get("needs_review") else ""
            lines.append(f"   {c['id']} {c['type']}({args}) {c['confidence']:.2f}: {c['text']}{flag}")
        if s["common_mistakes"]:
            lines.append("   mistakes: " + "; ".join(s["common_mistakes"]))
    return "\n".join(lines)


async def dry_run(src: Path, info: dict) -> None:
    tmp = RUNTIME / "builds" / "_dry-run"
    tmp.mkdir(parents=True, exist_ok=True)
    video = tmp / "video.mp4"
    await to_mp4(src, video, info)
    body = plan_request("<file id>", info["duration"])
    shown = json.loads(json.dumps(body))
    shown["text"]["format"]["schema"] = f"<config/schemas/step_plan.json, {len(json.dumps(PLAN_SCHEMA))} chars>"
    print(json.dumps(shown, indent=1, ensure_ascii=False))
    out_max = MODELS["plan_max_output_tokens"] * PRICES["spark_output_per_m"] / 1e6
    file_id = await gateway.upload_file(video, 3600)
    try:
        n = await gateway.input_tokens(plan_request(file_id, info["duration"]))
        v = round(info["duration"] * VIDEO_TOKENS_PER_S)
        inp = (n + v) * PRICES["spark_input_per_m"] / 1e6
        print(f"\ninput tokens: {n:,} from POST /v1/responses/input_tokens (it leaves the video out) + ~{v:,} for "
              f"{info['duration']:.0f} s of video (~{VIDEO_TOKENS_PER_S}/s measured) → ~${inp:.4f}")
    except (GatewayError, Exception) as e:  # an unsupported pre-count isn't fatal for a dry run
        inp = None
        print(f"\ninput token count unavailable ({e}); input cost unknown")
    finally:
        await gateway.delete_file(file_id)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"output: at most {MODELS['plan_max_output_tokens']:,} tokens (incl. reasoning) → at most ${out_max:.4f}")
    print(f"SAM 3.1 references: ${PRICES['sam_per_image']} per object per step (~$0.005–0.0075 a step)")
    if inp is not None:
        print(f"Muse Spark call: ${inp:.4f} + output, at most ${inp + out_max:.4f} (x2 if the plan needs its one retry)")


def bundle(tid: str, title: str | None, plan: dict, out: Path, duration: float, source: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    plan = rehash({**plan, "reviewed": False})
    (fixtures / "plans" / f"{tid}.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    dst = DATA / "tutorials" / tid
    if out != dst:
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(out, dst, ignore=shutil.ignore_patterns("plan.json"))
    listing = json.loads((fixtures / "tutorials.json").read_text(encoding="utf-8"))
    entry = next((t for t in listing if t["id"] == tid), None)
    if entry is None:
        entry = {"id": tid, "title": title or tid.replace("-", " ").capitalize(), "source": "bundled",
                 "owner_visitor": None, "creator": None, "licence": None, "duration_s": 0, "step_count": 0,
                 "status": "ready", "progress": None, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "delete_after": None}
        listing.append(entry)
    entry.update(duration_s=round(duration, 1), step_count=len(plan["steps"]), **({"title": title} if title else {}))
    (fixtures / "tutorials.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    hashes = json.loads((fixtures / "source_hashes.json").read_text(encoding="utf-8"))  # an upload of either opens it
    for f in (source, dst / "video.mp4"):
        hashes[hashlib.sha256(f.read_bytes()).hexdigest()] = tid
    (fixtures / "source_hashes.json").write_text(json.dumps(hashes, indent=1) + "\n", encoding="utf-8")


async def build(a) -> None:
    src = Path(a.video).resolve()
    info = await probe(src)
    check_upload(info, 600, True)
    if a.dry_run:
        return await dry_run(src, info)
    out = Path(a.out) if a.out else RUNTIME / "builds" / a.id
    shutil.rmtree(out, ignore_errors=True)
    t0 = time.monotonic()
    plan, stats = await process(src, out, a.id, info, lambda p: print(f"  {p}", file=sys.stderr),
                                transcript=True if a.transcript else None)
    (out / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(summary(plan))
    stats["wall_ms"] = round((time.monotonic() - t0) * 1000)
    print("\n" + json.dumps(stats), file=sys.stderr)
    if a.bundle:
        bundle(a.id, a.title, plan, out, info["duration"], src)
        print(f"\nBundled: server/fixtures/plans/{a.id}.json, data/tutorials/{a.id}/ (video, frames, refs), "
              "tutorials.json. Review the plan above, edit the JSON by hand if needed, then commit.", file=sys.stderr)
    else:
        print(f"\nWritten to {out}", file=sys.stderr)


def promote(upload: str, tid: str, title: str | None) -> None:
    """A ready upload → the bundled tutorials (plan, video, frames, refs, details, credit): no model calls."""
    src = RUNTIME / "tutorials" / upload
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    plan = json.loads((src / "plan.json").read_text(encoding="utf-8"))
    m = meta.get("meta", meta)
    if m.get("status") != "ready":
        raise SystemExit(f"Upload {upload} is {m.get('status')}, not ready.")
    bundle(tid, title or m["title"], {**plan, "tutorial_id": tid}, src, m["duration_s"], src / "video.mp4")
    fixtures = Path(__file__).parent / "fixtures"
    listing = json.loads((fixtures / "tutorials.json").read_text(encoding="utf-8"))
    entry = next(t for t in listing if t["id"] == tid)
    if m.get("creator"):
        entry.update(creator=m["creator"], licence="Creative Commons video, credited")
    (fixtures / "tutorials.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"bundled {upload} as {tid}: {len(plan['steps'])} steps, by {m.get('creator')}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m server.tutorials")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="turn a tutorial video into a step plan, end frames and reference outlines")
    b.add_argument("video")
    b.add_argument("--id", required=True, help="tutorial id (slug), e.g. thermostat-wiring")
    b.add_argument("--bundle", action="store_true", help="write it into the bundled tutorials (fixtures + data/)")
    b.add_argument("--title", help="title on S1 (a new bundled tutorial; default from the id)")
    b.add_argument("--out", help="output folder without --bundle (default $FA_DATA_DIR/builds/<id>/)")
    b.add_argument("--dry-run", action="store_true", help="show the request and the estimated cost, call nothing billed")
    b.add_argument("--transcript", action="store_true",
                   help="also send the narration as text (Muse Voice Transcribe; default: models.toml plan_transcript)")
    pr = sub.add_parser("promote", help="bundle a processed upload (or pasted link) as a built-in tutorial")
    pr.add_argument("upload", help="the upload's id ($FA_DATA_DIR/tutorials/<id>/)")
    pr.add_argument("--id", required=True, help="tutorial id (slug) for the bundled copy")
    pr.add_argument("--title")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", a.id):
        raise SystemExit("--id must be a lowercase slug, e.g. thermostat-wiring")
    if a.cmd == "promote":
        return promote(a.upload, a.id, a.title)
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", a.id):
        raise SystemExit("--id must be a lowercase slug, e.g. thermostat-wiring")
    try:
        asyncio.run(build(a))
    except (Rejected, Failed) as e:
        raise SystemExit(str(e))
    except GatewayError as e:
        raise SystemExit(f"Meta call failed: {e}")


if __name__ == "__main__":
    main()


# ---- paste a link (YouTube / Vimeo) instead of uploading a file ----

def link_ok(url: str) -> bool:
    """Only https links on the allowed video hosts: never an arbitrary address fetched by the server (SSRF)."""
    from urllib.parse import urlparse
    from .config import LINK_HOSTS
    u = urlparse(url.strip())
    return u.scheme == "https" and (u.hostname or "").lower() in LINK_HOSTS and not u.username and not u.password


def cc_licence(info: dict) -> str | None:
    """The video's Creative Commons licence as the site reports it (YouTube: "Creative Commons Attribution license
    (reuse allowed)"; Vimeo: "by", "by-sa", "cc0"...), or None for a standard licence or none reported."""
    lic = (info.get("license") or "").strip()
    return lic if re.search(r"creative commons|^cc0$|^(cc[- ])?by(-(sa|nd|nc))*$", lic, re.I) else None


def fetch_link(url: str, max_s: float, dest: Path) -> dict:
    """Blocking (run in a thread): look up the video, refuse it if it's too long, then download at most 720p as MP4
    to `dest`. Returns {"path", "title", "creator", "duration"}. Raises Rejected."""
    import yt_dlp
    base = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "ffmpeg_location": ffmpeg(),
            "socket_timeout": 20, "max_filesize": 300 * 1024 * 1024}
    try:
        with yt_dlp.YoutubeDL(base) as y:
            info = y.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001 the site said no (private, removed, region-locked, not a video)
        raise Rejected("invalid_upload", "We couldn't open that link. Check it plays in your browser, or upload the file.") from e
    duration = info.get("duration") or 0
    if not duration:
        raise Rejected("invalid_upload", "That link isn't a single video. Paste the link of one video.")
    if not cc_licence(info):  # we only use videos whose creator allows reuse; a standard licence doesn't
        raise Rejected("not_licensed", "That video isn't Creative Commons licensed, so we can't use it. On YouTube, "
                       "filter your search by Creative Commons, or upload a video you have the rights to.")
    if duration > max_s:
        raise Rejected("too_long", f"That video is {int(duration // 60)} min long; the limit here is {int(max_s // 60)} min.")
    opts = {**base, "outtmpl": str(dest / "link.%(ext)s"), "merge_output_format": "mp4",
            "format": "bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/b[height<=720][vcodec^=avc1]/"  # H.264: remuxed, not re-encoded
                      "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b"}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
    except Exception as e:  # noqa: BLE001
        raise Rejected("invalid_upload", "The video didn't download. Try again, or upload the file.") from e
    files = sorted(dest.glob("link.*"), key=lambda f: f.stat().st_size)
    if not files:
        raise Rejected("invalid_upload", "The video didn't download. Try again, or upload the file.")
    return {"path": files[-1], "title": info.get("title") or "Video", "creator": info.get("channel") or info.get("uploader"),
            "licence": cc_licence(info),
            "duration": duration}
