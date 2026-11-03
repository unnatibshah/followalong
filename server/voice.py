"""Voice (TD §4.6, API spec §3): command matching, push-to-talk WAV checks, questions (prompt spec P3) and the realtime
proxy. Nothing here stores audio, transcripts, questions or answers; logs carry lengths and timings only."""

import asyncio
import io
import json
import logging
import re
import time
import unicodedata
import wave

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from .config import COACH_PROMPT, MODELS, QUESTION_PROMPT, TIMEOUTS, VOICE, WATCH_PROMPT
from .gateway import CapReached, GatewayError, gateway
from .sam import output_text
from .spark import done_text, jpeg

# Whole phrases only (TD §4.6), after lower-casing, dropping punctuation and a polite "ok"/"please".
COMMANDS = {
    "next": ["next", "next step", "next one", "go next", "move on", "skip ahead"],
    "again": ["again", "repeat", "repeat that", "show me again", "one more time", "replay", "play it again", "rewind"],
    "check_me": ["check me", "check", "check it", "check this", "check now", "check my work", "done", "i'm done", "im done"],
    "pause": ["pause", "stop", "wait", "hold on", "pause it"],
    "play": ["play", "resume", "continue", "go", "keep going", "go on", "unpause"],
}
COMMANDS["play"] += ["start", "let's start", "lets start", "let's go", "lets go", "i'm ready", "im ready", "ready"]
# The coach's other languages (server/i18n.py): the words its spoken lines ask for ("di listo"), so a safety step and a
# yes/no question work without English. Everything else in another language goes to the coach as a question.
COMMANDS_INTL = {
    "safe": ["listo", "lista", "prêt", "prête", "c'est bon", "fertig", "bereit", "pronto", "pronta", "तैयार", "हो गया"],
    "yes": ["sí", "si", "oui", "ja", "sim", "हाँ", "हां", "haan"],
    "no": ["non", "nein", "não", "nao", "नहीं", "nahin"],
}
COMMANDS |= {
    "back": ["back", "go back", "go back a step", "previous step", "previous", "last step", "step back", "back one step"],
    "skip": ["skip", "skip this", "skip this step", "skip it"],
    "yes": ["yes", "yeah", "yep", "yup", "it does", "yes it does", "correct", "looks right", "looks the same"],
    "no": ["no", "nope", "it doesn't", "no it doesn't", "not really", "doesn't look like it"],
    # a safety step's confirmation ("the breaker is off"): only the learner can know, the camera can't see power
    "safe": ["safe", "it's safe", "its safe", "it is safe", "all safe", "power is off", "the power is off",
             "power's off", "breaker is off", "the breaker is off", "breaker's off", "done that", "i did that"],
}
PHRASE = {p: name for name, phrases in COMMANDS.items() for p in phrases}
PHRASE |= {p: name for name, phrases in COMMANDS_INTL.items() for p in phrases if p not in PHRASE}
# Anywhere in the sentence, so "wait, what did you say?" and "hey, am I doing this right?" work hands-free.
INTENTS = [
    ("repeat", re.compile(r"\b(what did you (just )?say|say (that|it) again|come again|repeat (that|yourself|it)|"
                          r"(didn't|did not|couldn't) (hear|catch) (that|you|it)|pardon|sorry what)\b")),
    ("check_me", re.compile(r"\b((am i|are we) (doing )?(it|this|that)? ?(right|correct|ok|okay|good)|"
                            r"(is|was) (this|that|it) (right|correct|ok|okay|good|how it should be)|"
                            r"(does|do) (this|it|that|mine) look (right|good|ok|okay|correct)|"
                            r"did i (do|get) (it|this|that) right|how('s| is| am) (it|this|that|i) (doing|looking)|"
                            r"(check|look at) (this|it|my work|that)( now)?)\b")),
    # the learner disagrees with the coach: "no, it's right", "you're wrong", "mine is fine"
    ("override", re.compile(r"\b((it's|it is|that's|that is|mine is|mine's|this is) (actually )?(right|correct|fine|good)|"
                            r"you('re| are) wrong|(it|this|mine) (looks|is) (right|fine|correct) to me|"
                            r"i did it right|it's how (it|mine) is supposed to be|nothing's wrong|nothing is wrong)\b")),
]
KEYWORDS = ["next", "again", "check me", "pause", "play", "yes", "no", "repeat", "am I doing it right", "it's safe",
            "it's right"]  # recognition hints for the realtime handshake
FILLER = re.compile(r"^(?:(?:ok|okay|hey|please|um|uh|so)\s+)+|(?:\s+(?:please|now|thanks|thank you))+$")
CLIP_MAX_S = 10.0
log = logging.getLogger("followalong.voice")
RATE = 24000
BYTES_PER_S = RATE * 2


NUMBERS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                         "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUMBERS |= {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
            "ninth": 9, "tenth": 10, "to": 2, "too": 2, "for": 4}
STEP_RX = re.compile(r"^(?:(?:go|jump|skip|take me|move|back) (?:back )?to )?(?:the )?(?:step (\w+)|(\w+) step)$")


def command(transcript: str) -> str | None:
    """`next`, `again`, `check_me`, `pause`, `play`, `yes`, `no`, `back`, `skip`, `repeat`, `safe`, `override`,
    `step_<n>` (go to step n), or None (a question for the coach). Whole phrases for the short commands; `repeat` and `check_me` also match
    inside a longer sentence."""
    # letters, digits, spaces, apostrophes and combining marks (Devanagari vowel signs are marks, not \w)
    t = "".join(ch if ch.isalnum() or ch.isspace() or ch == "'" or unicodedata.category(ch)[0] == "M" else " "
                for ch in transcript.lower())
    t = FILLER.sub("", " ".join(t.split())).strip()
    if t in PHRASE:
        return PHRASE[t]
    if m := STEP_RX.match(t):
        word = m[1] or m[2]
        n = int(word) if word.isdigit() else NUMBERS.get(word)
        if n and (m[1] or word in NUMBERS and not word.isdigit() and m[2]):
            return f"step_{n}"
    return next((name for name, rx in INTENTS if rx.search(t)), None)


def clip_seconds(body: bytes) -> float:
    """Length of a push-to-talk clip. Raises ValueError unless it's a WAV, mono, 16-bit PCM, 24 kHz, with audio."""
    try:
        with wave.open(io.BytesIO(body)) as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError) as e:
        raise ValueError("Send a WAV clip (mono, 16-bit, 24 kHz).") from e
    if (ch, width, rate) != (1, 2, RATE):
        raise ValueError(f"The clip must be mono, 16-bit, 24 kHz (got {ch} channel(s), {width * 8}-bit, {rate} Hz).")
    if n == 0:
        raise ValueError("The clip is empty.")
    return n / rate


def two_sentences(text: str, n: int = 2) -> str:
    return " ".join(re.split(r"(?<=[.!?])\s+", " ".join(text.split()))[:n])


def coach_request(plan: dict, details: dict, step: dict, meta: dict, frame_url: str, end_url: str | None) -> dict:
    """The voice coach (prompt `coach`): the whole lesson, the current step's exact directions, the last check, the
    recent conversation (sent by the browser, never stored), the live frame and the step's finished frame."""
    steps = plan["steps"]
    d = details.get(str(step["id"])) or {}
    nxt = next((s for s in steps if s["id"] == step["id"] + 1), None)
    nd = details.get(str(nxt["id"])) if nxt else None
    hist = [h for h in (meta.get("history") or []) if isinstance(h, dict) and isinstance(h.get("text"), str)][-8:]
    fill = {
        "title": plan.get("title") or plan["tutorial_id"], "actor": plan.get("actor") or "hand",
        "outline": "\n".join(f"{s['id']}. {(details.get(str(s['id'])) or {}).get('say') or s['instruction']}" for s in steps),
        "id": step["id"], "instruction": step["instruction"], "say": d.get("say") or step["instruction"],
        "placements": "\n".join(f"- {x}" for x in d.get("placements") or []) or "- (none recorded)",
        "done_looks_like": done_text(step, d.get("check_yourself") or step.get("done_looks_like", "")),
        "watch_for": "; ".join(d.get("watch_for") or step.get("common_mistakes") or []) or "nothing noted",
        "next": (f"step {nxt['id']}: {(nd or {}).get('say') or nxt['instruction']}" if nxt else "none, this is the last step"),
        "last_check": str(meta.get("last_check") or "none yet")[:400],
        "history": "\n".join(f"{'Learner' if h.get('role') == 'user' else 'Coach'}: {h['text'][:300]}" for h in hist) or "(none)",
        "question": meta["question"].replace('"', "'"), "language": meta.get("language") or "English",
    }
    text = COACH_PROMPT["user"]
    for k, v in fill.items():
        text = text.replace("{" + k + "}", str(v))
    content = [{"type": "input_text", "text": text}, {"type": "input_text", "text": "LEARNER (camera now):"},
               {"type": "input_image", "image_url": frame_url}]
    if end_url:
        content += [{"type": "input_text", "text": "TUTORIAL (this step finished):"}, {"type": "input_image", "image_url": end_url}]
    return {"model": MODELS["judged"], "instructions": COACH_PROMPT["system"],
            "input": [{"type": "message", "role": "user", "content": content}],
            "reasoning": {"effort": MODELS["judged_effort"]}, "max_output_tokens": MODELS["question_max_output_tokens"]}


async def coach(plan: dict, details: dict, step: dict, meta: dict, frame: bytes, end_frame: bytes | None,
                session_id: str) -> dict:
    """{"answer", "latency_ms", "cost_usd"}: the coach's spoken reply (at most four sentences). Raises GatewayError."""
    t0 = time.monotonic()
    urls = await asyncio.gather(asyncio.to_thread(jpeg, frame), *([asyncio.to_thread(jpeg, end_frame)] if end_frame else []))
    data, usd = await gateway.responses(coach_request(plan, details, step, meta, urls[0], urls[1] if end_frame else None),
                                        TIMEOUTS["question_s"], session_id)
    text = two_sentences(output_text(data).replace("*", ""), 4)
    if not text:
        reason = (data.get("incomplete_details") or {}).get("reason")
        raise GatewayError(f"incomplete_{reason}" if data.get("status") == "incomplete" else "invalid_output")
    return {"answer": text, "latency_ms": round((time.monotonic() - t0) * 1000), "cost_usd": usd}


def question_request(step: dict, question: str, frame_url: str) -> dict:
    fill = {"id": step.get("id", ""), "instruction": step.get("instruction", ""),
            "done_looks_like": done_text(step, step.get("done_looks_like", "")), "question": question.replace('"', "'")}
    text = QUESTION_PROMPT["user"]
    for k, v in fill.items():
        text = text.replace("{" + k + "}", str(v))
    return {"model": MODELS["judged"], "instructions": QUESTION_PROMPT["system"],
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": text}, {"type": "input_image", "image_url": frame_url}]}],
            "reasoning": {"effort": MODELS["judged_effort"]}, "max_output_tokens": MODELS["question_max_output_tokens"]}


async def answer(step: dict, question: str, frame: bytes, session_id: str) -> dict:
    """{"answer", "latency_ms", "cost_usd"} from Muse Spark (minimal reasoning) on the live frame. Raises GatewayError."""
    t0 = time.monotonic()
    data, usd = await gateway.responses(question_request(step, question, await asyncio.to_thread(jpeg, frame)),
                                        TIMEOUTS["question_s"], session_id)
    text = two_sentences(output_text(data))
    if not text:  # `incomplete_max_output_tokens`: reasoning used the whole budget before any answer text
        reason = (data.get("incomplete_details") or {}).get("reason")
        raise GatewayError(f"incomplete_{reason}" if data.get("status") == "incomplete" else "invalid_output")
    return {"answer": text, "latency_ms": round((time.monotonic() - t0) * 1000), "cost_usd": usd}


def audio_left(session: dict) -> float:
    """FA_VOICE_MAX_SECONDS per session (cost guard, security T15). The count lives in budget.json (`audio_s`), so a
    restart keeps it; the session dict caches it."""
    if "audio_s" not in session:
        session["audio_s"] = gateway.budget.audio_used(session["id"])
    return VOICE["max_s"] - session["audio_s"]


def count_audio(session: dict, seconds: float) -> None:
    audio_left(session)  # load the saved count first
    session["audio_s"] += seconds
    gateway.budget.add_audio(session["id"], seconds)


async def proxy(ws: WebSocket, session: dict, on_refused, languages: list[str] | None = None) -> None:
    """API spec §3, after the socket is accepted. `{"type": "start"}` opens Muse Voice Transcribe realtime; binary PCM
    frames are relayed; partial transcripts become `caption`s; each `speechComplete` turn becomes one `command` or
    `question`. Closes after FA_WS_IDLE_SECONDS with no audio, or when the session's audio seconds run out.
    `on_refused()` is called if Meta refuses the session, so /api/config offers push-to-talk for a while."""
    send = ws.send_json
    try:
        first = await asyncio.wait_for(ws.receive_json(), VOICE["idle_s"])
    except (TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        return await _close(ws)
    if first.get("type") != "start":
        return await _close(ws, 1008)
    if audio_left(session) <= 0:
        await send({"type": "error", "code": "cap_reached"})
        return await _close(ws)
    before = session["audio_s"]
    try:
        async with gateway.realtime(session["id"], KEYWORDS, languages) as (up, meter):
            await send({"type": "ready"})
            stopping = asyncio.Event()
            down = asyncio.create_task(_downstream(up, send, stopping))
            upstream_task = asyncio.create_task(_upstream(ws, up, meter, session, stopping))
            await asyncio.wait({down, upstream_task}, return_when=asyncio.FIRST_COMPLETED)
            ok = upstream_task.done() and upstream_task.exception() is None
            reason = upstream_task.result() if ok else None
            if reason in ("stop", "cap") and not down.done():  # endStream sent: let Meta flush the last turn
                await asyncio.wait({down}, timeout=5)
            for t in (down, upstream_task):
                t.cancel()
            await asyncio.gather(down, upstream_task, return_exceptions=True)  # Meta or the browser may have dropped
            if reason == "cap":
                await _send_quietly(send, {"type": "error", "code": "cap_reached"})
    except CapReached:
        await _send_quietly(send, {"type": "error", "code": "cap_reached"})
    except GatewayError as e:
        if e.code == "voice_unavailable":
            on_refused()
        await _send_quietly(send, {"type": "error", "code": "voice_unavailable"})
    except WebSocketDisconnect:
        pass
    finally:  # the stream's seconds, saved once at its end. ponytail: a crash mid-stream loses that stream's count
        gateway.budget.add_audio(session["id"], session["audio_s"] - before)
    await _close(ws)


async def _upstream(ws: WebSocket, up, meter: dict, session: dict, stopping: asyncio.Event) -> str:
    """Browser → Meta. Returns why it ended: "stop", "idle", "cap" or "gone"."""
    while True:
        try:
            m = await asyncio.wait_for(ws.receive(), VOICE["idle_s"])
        except TimeoutError:
            return "idle"
        if m["type"] == "websocket.disconnect":
            return "gone"
        if m.get("bytes"):
            pcm = m["bytes"]
            if audio_left(session) < len(pcm) / BYTES_PER_S:
                stopping.set()
                await up.send(json.dumps({"type": "endStream"}))
                return "cap"
            session["audio_s"] += len(pcm) / BYTES_PER_S
            meter["bytes"] += len(pcm)
            await up.send(pcm)
        elif m.get("text"):
            try:
                kind = json.loads(m["text"]).get("type")
            except (ValueError, AttributeError):
                continue
            if kind == "stop":
                stopping.set()
                await up.send(json.dumps({"type": "endStream"}))
                return "stop"


async def _downstream(up, send, stopping: asyncio.Event) -> None:
    """Meta → browser. Partials are captions; a turn is matched only on `speechComplete` (keyed by `turnId`)."""
    try:
        await _relay(up, send)
    except websockets.ConnectionClosed:
        pass
    if not stopping.is_set():  # Meta closed the stream on its own
        await _send_quietly(send, {"type": "error", "code": "voice_unavailable"})


async def _relay(up, send) -> None:
    turn = None
    async for raw in up:
        if isinstance(raw, bytes):
            continue
        e = json.loads(raw)
        kind = e.get("type")
        if kind == "speechStart":
            turn = e.get("turnId")
        elif kind == "transcript" and not e.get("final") and e.get("transcript"):
            await send({"type": "caption", "turn_id": turn, "text": e["transcript"]})
        elif kind == "speechComplete":
            text = (e.get("transcript") or "").strip()
            if not text:
                continue
            name = command(text)
            await send({"type": "command", "turn_id": e.get("turnId"), "name": name} if name
                       else {"type": "question", "turn_id": e.get("turnId"), "text": text})
        elif kind == "error":
            return


async def _send_quietly(send, msg: dict) -> None:
    try:
        await send(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _close(ws: WebSocket, code: int = 1000) -> None:
    try:
        await ws.close(code)
    except (RuntimeError, WebSocketDisconnect):  # already closed, or the page went away (DEF-021)
        pass


# ---- the live watcher: one look every 2–3 s while the learner works (prompt `watch`) ----

WATCH_STATUS = ["working", "mistake", "cant_see", "looks_done", "idle"]
WATCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["status", "observation", "on_action", "substitute", "say"],
                "properties": {"status": {"type": "string", "enum": WATCH_STATUS},
                               "observation": {"type": "string"}, "on_action": {"type": "integer"},
                               "substitute": {"type": "string"}, "say": {"type": "string"}}}


def actions(say: str) -> list[str]:
    """A step's spoken directions as separate actions (its sentences): the lesson shows and says one at a time."""
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", say or "") if x.strip()]


def watch_request(plan: dict, details: dict, step: dict, meta: dict, frame_url: str, end_url: str | None) -> dict:
    d = details.get(str(step["id"])) or {}
    lines = lambda xs, n: "\n".join(f"- {str(x)[:200]}" for x in (xs or [])[-n:] if isinstance(x, str)) or "(none yet)"  # noqa: E731
    fill = {"title": plan.get("title") or plan["tutorial_id"], "actor": plan.get("actor") or "hand", "id": step["id"],
            "instruction": step["instruction"], "say": d.get("say") or step["instruction"],
            "actions": "\n".join(f"{i}. {x}" for i, x in enumerate(actions(d.get("say") or step["instruction"]), 1)),
            "placements": "\n".join(f"- {x}" for x in d.get("placements") or []) or "- (none recorded)",
            "watch_for": "; ".join(d.get("watch_for") or step.get("common_mistakes") or []) or "nothing noted",
            "done_looks_like": done_text(step, d.get("check_yourself") or step.get("done_looks_like", "")),
            "elapsed": int(meta.get("elapsed_s") or 0), "encourage": "yes" if meta.get("encourage") else "no",
            "recent": lines(meta.get("recent"), 5), "said": lines(meta.get("said"), 3),
            "language": meta.get("language") or "English"}
    text = WATCH_PROMPT["user"]
    for k, v in fill.items():
        text = text.replace("{" + k + "}", str(v))
    content = [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": frame_url}]
    if end_url:
        content.append({"type": "input_image", "image_url": end_url})
    return {"model": MODELS["judged"], "instructions": WATCH_PROMPT["system"],
            "input": [{"type": "message", "role": "user", "content": content}],
            "reasoning": {"effort": MODELS["judged_effort"]}, "max_output_tokens": MODELS["judged_max_output_tokens"],
            "text": {"format": {"type": "json_schema", "name": "watch", "schema": WATCH_SCHEMA, "strict": True}}}


def clean_watch(raw: dict) -> dict:
    """Validated again in code: a known status, short strings, nothing said for looks_done (the measured check speaks)."""
    status = raw.get("status") if raw.get("status") in WATCH_STATUS else "working"
    say = two_sentences(str(raw.get("say") or "").replace("*", ""), 2)[:300]
    on = raw.get("on_action")
    return {"status": status, "observation": " ".join(str(raw.get("observation") or "").split())[:200],
            "on_action": on if isinstance(on, int) and 1 <= on <= 20 else None,
            "substitute": " ".join(str(raw.get("substitute") or "").split())[:80],
            "say": "" if status == "looks_done" else say}


async def watch(plan: dict, details: dict, step: dict, meta: dict, frame: bytes, end_frame: bytes | None,
                session_id: str) -> dict:
    """{"status", "observation", "say", "latency_ms", "cost_usd"}. Raises GatewayError."""
    t0 = time.monotonic()
    urls = await asyncio.gather(asyncio.to_thread(jpeg, frame), *([asyncio.to_thread(jpeg, end_frame)] if end_frame else []))
    data, usd = await gateway.responses(watch_request(plan, details, step, meta, urls[0], urls[1] if end_frame else None),
                                        TIMEOUTS["question_s"], session_id, priority="background")
    text = output_text(data).strip()
    try:
        raw = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError as e:
        raise GatewayError("invalid_output") from e
    return {**clean_watch(raw), "latency_ms": round((time.monotonic() - t0) * 1000), "cost_usd": usd}
