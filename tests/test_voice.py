"""Voice (FR-90-93, API spec §3): command mapping, push-to-talk WAV checks, audio caps, questions log only their length,
the realtime proxy against a fake upstream, the /api/config fallback. `-m live` calls Meta (well under $0.01)."""

import asyncio
import io
import json
import logging
import math
import struct
import time
import wave

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import config, providers
from server.gateway import gateway
from server.main import app
from server import voice
from server.voice import command, clip_seconds, two_sentences

KEY = "sk-test-not-a-real-key"
def _jpeg() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (64, 36), "grey").save(out, "JPEG")
    return out.getvalue()


JPEG = _jpeg()


def make_wav(seconds: float, rate: int = 24000, channels: int = 1, width: int = 2, tone: bool = False) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(seconds * rate)
        if tone:
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * 440 * 2 * math.pi / rate))) for i in range(n)))
        else:
            w.writeframes(b"\x00" * n * channels * width)
    return out.getvalue()


@pytest.fixture
def c():
    return TestClient(app)


def session(c, mode="live"):
    body = {"tutorial_id": "install-thermostat", "mode": mode, "age_confirmed": True}
    if mode == "replay":
        body["recording_id"] = "with-mistakes"
    return c.post("/api/sessions", json=body).json()["session_id"]


@pytest.fixture
def meta(monkeypatch):
    """A key and a fake Meta: every HTTP call is answered by `meta.handler` and recorded in `meta.seen`."""
    monkeypatch.setenv("MODEL_API_KEY", KEY)

    class M:
        seen = []
        handler = None
    M.seen = []

    def handle(r):
        M.seen.append(r)
        return M.handler(r)
    monkeypatch.setattr(gateway, "_transport", httpx.MockTransport(handle))
    monkeypatch.setattr(gateway, "_client", None)  # the next call builds a client on the mock transport
    monkeypatch.setattr(providers.voice, "refused_at", None)
    return M


@pytest.mark.parametrize("said,name", [
    ("next", "next"), ("Next.", "next"), ("NEXT STEP!", "next"), ("okay, next please", "next"),
    ("again", "again"), ("Show me again.", "again"), ("Check me.", "check_me"), ("check", "check_me"),
    ("I'm done", "check_me"), ("pause", "pause"), ("Hold on", "pause"), ("Play.", "play"), ("resume", "play"),
    ("What did I do wrong?", None), ("next to the red wire?", None), ("Which terminal does the blue wire go in?", None),
    ("", None)])
def test_command_mapping_whole_phrase_case_insensitive(said, name):
    assert command(said) == name


def test_clip_seconds_accepts_only_mono_16_bit_24khz_wav():
    assert clip_seconds(make_wav(1.5)) == pytest.approx(1.5)
    for bad in (make_wav(1, channels=2), make_wav(1, rate=16000), make_wav(1, width=1), b"RIFF", b"not audio", make_wav(0)):
        with pytest.raises(ValueError):
            clip_seconds(bad)


def test_transcribe_route_checks_the_clip_before_any_model_call(c, meta):
    sid = session(c)
    url = f"/api/sessions/{sid}/transcribe"
    r = c.post(url, content=make_wav(10.5), headers={"content-type": "audio/wav"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_long"
    r = c.post(url, content=make_wav(1, rate=44100), headers={"content-type": "audio/wav"})
    assert r.status_code == 400 and "24 kHz" in r.json()["error"]["message"]
    assert meta.seen == []
    replay = session(c, "replay")  # the coach talks in replays too: the same clip rules apply
    assert c.post(f"/api/sessions/{replay}/transcribe", content=make_wav(10.5)).status_code == 413


def test_transcribe_without_key_uses_the_fixture(c, scripted):
    sid = session(c)
    assert c.post(f"/api/sessions/{sid}/transcribe", content=make_wav(1)).json() == {"transcript": "next", "command": "next"}


def test_transcribe_calls_muse_voice_file_mode_and_counts_audio(c, meta):
    meta.handler = lambda r: httpx.Response(200, json={"sessionId": "x", "transcript": "Check me.", "audioDurationMs": 640, "turns": []})
    sid = session(c)
    r = c.post(f"/api/sessions/{sid}/transcribe", content=make_wav(0.64), headers={"content-type": "audio/wav"})
    assert r.json() == {"transcript": "Check me.", "command": "check_me"}
    req = meta.seen[0]
    assert req.url.path == "/v1/asr/transcribe" and req.headers["authorization"] == f"Bearer {KEY}"
    body = req.content.decode("latin-1")
    assert '"audioEncoding": "WAV"' in body and '"model": "muse-voice-transcribe-1.0"' in body and "audio/wav" in body
    assert gateway.budget.snapshot()["sessions"][sid] == pytest.approx(0.18 * 0.64 / 3600, abs=1e-6)
    meta.handler = lambda r: httpx.Response(200, json={"transcript": "What did I do wrong?", "audioDurationMs": 1200})
    assert c.post(f"/api/sessions/{sid}/transcribe", content=make_wav(1.2)).json() == {"transcript": "What did I do wrong?"}


def test_audio_seconds_cap_per_session(c, monkeypatch, scripted):
    monkeypatch.setitem(config.VOICE, "max_s", 2)
    sid = session(c)
    url = f"/api/sessions/{sid}/transcribe"
    assert c.post(url, content=make_wav(1.5)).status_code == 200
    assert gateway.budget.snapshot()["audio_s"][sid] == 1.5  # in budget.json, next to the dollar counters
    providers.sessions.sessions.clear()  # a restart: the session is rebuilt from its log, the count from budget.json
    r = c.post(url, content=make_wav(1))
    assert r.status_code == 429 and r.json()["error"]["code"] == "cap_reached"


def test_coach_answers_in_four_sentences_with_the_lesson_and_history_and_logs_only_the_length(c, meta, caplog):
    question = "Which terminal does the blue wire go in?"
    meta.handler = lambda r: httpx.Response(200, json={
        "output_text": "The blue wire goes in C.  Loosen the C screw first. Then **tighten** it. Tug it. Done.",
        "usage": {"input_tokens": 900, "output_tokens": 40}})
    sid = session(c)
    history = [{"role": "user", "text": "what's next"}, {"role": "coach", "text": "Strip the white wire."}]
    with caplog.at_level(logging.INFO, logger="followalong"):
        r = c.post(f"/api/sessions/{sid}/questions", files={"frame": ("f.jpg", JPEG, "image/jpeg")},
                   data={"meta": json.dumps({"step_id": 4, "question": question, "question_no": 1, "history": history,
                                             "last_check": "wrong: red wire not in Rc"})})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "The blue wire goes in C. Loosen the C screw first. Then tighten it. Tug it."
    sent = json.loads(meta.seen[0].content)
    assert sent["model"] == "muse-spark-1.3" and sent["reasoning"] == {"effort": "minimal"} and sent["store"] is False
    text = sent["input"][0]["content"][0]["text"]
    assert sent["max_output_tokens"] == 1500 and question in text and "Coach: Strip the white wire." in text
    assert "wrong: red wire not in Rc" in text and "Outline:" in text
    assert sent["input"][0]["content"][2]["image_url"].startswith("data:image/jpeg;base64,")  # the learner
    assert sent["input"][0]["content"][4]["image_url"].startswith("data:image/jpeg;base64,")  # the tutorial
    log = (config.RUNTIME / "sessions" / f"{sid}.jsonl").read_text(encoding="utf-8")
    q = [json.loads(line) for line in log.splitlines() if '"question"' in line][0]
    assert q["data"] == {"length": len(question), "question_no": 1}
    assert "blue wire" not in log and "blue wire" not in caplog.text and KEY not in caplog.text


def test_question_fallback_logs_the_gateway_code(c, meta, caplog):
    """CP3's 1-in-5 fallback: a reasoning-only answer cut off at max_output_tokens. The log line names it (no text)."""
    meta.handler = lambda r: httpx.Response(200, json={
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": [],
        "usage": {"input_tokens": 900, "output_tokens": 1500}})
    sid = session(c)
    with caplog.at_level(logging.INFO, logger="followalong"):
        r = c.post(f"/api/sessions/{sid}/questions", files={"frame": ("f.jpg", JPEG, "image/jpeg")},
                   data={"meta": json.dumps({"step_id": 4, "question": "why blue?", "question_no": 1})})
    assert r.json()["answer"].startswith("Muse Spark didn't answer")
    line = next(json.loads(x.message) for x in caplog.records if "question_fallback" in x.message)
    assert line == {"event": "question_fallback", "error": "incomplete_max_output_tokens", "status": None}
    assert "why blue" not in caplog.text


def test_question_needs_a_frame(c, scripted):
    sid = session(c)
    r = c.post(f"/api/sessions/{sid}/questions", data={"meta": json.dumps({"step_id": 4, "question": "why?", "question_no": 1})})
    assert r.status_code == 400


def test_config_voice_falls_back_to_push_to_talk(c, monkeypatch):
    assert c.get("/api/config").json()["voice"] == "push_to_talk"  # no key: the fixture answers push-to-talk clips
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    monkeypatch.setattr(providers.voice, "refused_at", None)
    assert c.get("/api/config").json()["voice"] == "realtime"
    providers.voice.refused()
    assert c.get("/api/config").json()["voice"] == "push_to_talk"
    monkeypatch.setattr(providers.voice, "refused_at", time.monotonic() - config.TIMEOUTS["realtime_retry_s"] - 1)
    assert c.get("/api/config").json()["voice"] == "realtime"  # the cool-down is over: realtime is tried again
    providers.voice.refused()
    monkeypatch.setitem(config.VOICE, "mode", "off")
    assert c.get("/api/config").json()["voice"] == "off"


# ---- realtime proxy, against a fake Muse Voice Transcribe ----

class FakeUpstream:
    """Answers the handshake with `ack`; after the first audio frame, plays `script`; ends on endStream."""

    def __init__(self, script, ack=None):
        self.script, self.ack, self.sent, self.q, self.url = script, ack or {"sessionId": "voyager/duplex/test"}, [], asyncio.Queue(), None

    async def send(self, m):
        self.sent.append(m)
        if isinstance(m, bytes) and sum(isinstance(x, bytes) for x in self.sent) == 1:
            for e in self.script:
                await self.q.put(json.dumps(e))
        if m == json.dumps({"type": "endStream"}):
            await self.q.put(None)

    async def recv(self):
        return json.dumps(self.ack)

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            raise StopAsyncIteration
        return m

    async def close(self):
        pass


SCRIPT = [{"type": "speechStart", "turnId": 0, "audioProcessedMs": 700},
          {"type": "transcript", "transcript": "Check", "final": False, "audioProcessedMs": 1840},
          {"type": "speechEnd", "turnId": 0, "audioProcessedMs": 3180},
          {"type": "speechComplete", "turnId": 0, "transcript": "Check me.", "audioProcessedMs": 3180},
          {"type": "speechStart", "turnId": 1, "audioProcessedMs": 4000},
          {"type": "speechComplete", "turnId": 1, "transcript": "Which terminal does the blue wire go in?", "audioProcessedMs": 6000}]


@pytest.fixture
def upstream(meta, monkeypatch):
    holder = {}

    async def connect(url, **kw):
        if holder.get("refuse"):
            raise OSError("refused")
        holder["up"].url = url
        return holder["up"]
    monkeypatch.setattr(gateway, "_ws_connect", connect)
    return holder


def ws_url(sid):
    return f"/ws/voice?session_id={sid}"


ORIGIN = {"origin": "http://testserver"}


def test_realtime_proxy_relays_captions_commands_and_questions(c, upstream):
    upstream["up"] = up = FakeUpstream(SCRIPT)
    sid = session(c)
    got = []
    with c.websocket_connect(ws_url(sid), headers=ORIGIN) as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json() == {"type": "ready"}
        ws.send_bytes(b"\x00" * 3840)  # 80 ms of silence
        for _ in range(3):
            got.append(ws.receive_json())
        ws.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert got == [{"type": "caption", "turn_id": 0, "text": "Check"},
                   {"type": "command", "turn_id": 0, "name": "check_me"},
                   {"type": "question", "turn_id": 1, "text": "Which terminal does the blue wire go in?"}]
    hs = json.loads(up.sent[0])
    assert hs["authorization"] == {"accessToken": f"Bearer {KEY}"} and hs["mode"] == "ENDPOINTING"
    assert hs["audioEncoding"] == "PCM_24KHZ" and hs["model"] == "muse-voice-transcribe-1.0" and "check me" in hs["keywords"]
    assert up.url == "wss://api.meta.ai/v1/asr/realtime" and up.sent[-1] == json.dumps({"type": "endStream"})
    assert KEY not in json.dumps(got)
    assert gateway.budget.snapshot()["sessions"][sid] == pytest.approx(0.18 * 0.08 / 3600, abs=1e-7)
    assert gateway.budget.snapshot()["audio_s"][sid] == 0.08  # the stream's seconds, saved when it ends


def test_realtime_refused_falls_back_to_push_to_talk(c, upstream):
    upstream["up"] = FakeUpstream([], ack={"type": "error", "message": "not enabled", "sessionId": ""})
    sid = session(c)
    assert c.get("/api/config").json()["voice"] == "realtime"
    with c.websocket_connect(ws_url(sid), headers=ORIGIN) as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json() == {"type": "error", "code": "voice_unavailable"}
    assert c.get("/api/config").json()["voice"] == "push_to_talk"


def test_realtime_audio_cap_and_idle_close(c, upstream, monkeypatch):
    upstream["up"] = FakeUpstream([])
    monkeypatch.setitem(config.VOICE, "max_s", 0.1)
    sid = session(c)
    with c.websocket_connect(ws_url(sid), headers=ORIGIN) as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json() == {"type": "ready"}
        ws.send_bytes(b"\x00" * 3840)   # 0.08 s: fine
        ws.send_bytes(b"\x00" * 3840)   # would pass 0.1 s
        assert ws.receive_json() == {"type": "error", "code": "cap_reached"}
    monkeypatch.setitem(config.VOICE, "max_s", 600)
    monkeypatch.setitem(config.VOICE, "idle_s", 0.2)
    upstream["up"] = FakeUpstream([])
    sid = session(c)
    with c.websocket_connect(ws_url(sid), headers=ORIGIN) as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json() == {"type": "ready"}
        with pytest.raises(WebSocketDisconnect):  # no audio for idle_s: closed
            ws.receive_json()


def test_voice_socket_refuses_other_origins_and_cookies(c, upstream):
    upstream["up"] = FakeUpstream([])
    sid = session(c)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(ws_url(sid), headers={"origin": "https://evil.example"}):
            pass
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect(ws_url(sid), headers=ORIGIN):  # another browser: no cookie
            pass


def test_close_after_the_page_went_away_is_quiet():
    """DEF-021: starlette raises WebSocketDisconnect from close() when the browser has gone; the proxy swallows it."""
    class Gone:
        async def close(self, code):
            raise WebSocketDisconnect(1006)
    asyncio.run(voice._close(Gone()))


def test_two_sentences():
    assert two_sentences("One. Two? Three!") == "One. Two?"
    assert two_sentences("  Just one  ") == "Just one"


@pytest.mark.live
def test_live_muse_voice_file_and_realtime_handshake():
    """Real Meta: a 1 s tone through /asr/transcribe (~$0.00005) and one realtime handshake (no audio)."""
    data, usd = asyncio.run(gateway.transcribe(make_wav(1, tone=True), 1.0, None))
    assert isinstance(data["transcript"], str) and data["audioDurationMs"] > 0 and usd < 0.001

    async def handshake():
        async with gateway.realtime("live-test", ["next"]) as (up, meter):
            await up.send(json.dumps({"type": "endStream"}))
    asyncio.run(handshake())


@pytest.mark.parametrize("said,name", [
    ("Wait, what did you say?", "repeat"), ("sorry, can you say that again", "repeat"), ("I didn't catch that", "repeat"),
    ("Am I doing it right?", "check_me"), ("hey is this right", "check_me"), ("Does this look right?", "check_me"),
    ("how am I doing", "check_me"), ("yeah", "yes"), ("Nope.", "no"),
    ("where does the red wire go", None), ("what is a resistor", None),
])
def test_hands_free_intents(said, name):
    assert command(said) == name


def test_watch_answer_is_validated_in_code():
    assert voice.clean_watch({"status": "looks_done", "observation": " knot  done ", "on_action": 3, "say": "Great!"}) == \
        {"status": "looks_done", "observation": "knot done", "on_action": 3, "substitute": "", "say": ""}  # the measured check announces it
    assert voice.clean_watch({"status": "nonsense", "on_action": 99, "say": "**Oh, wait**, wrong row. Pull it out. Then more. More."}) == \
        {"status": "working", "observation": "", "on_action": None, "substitute": "", "say": "Oh, wait, wrong row. Pull it out."}
    assert voice.actions("Hold the rope. Cross the end over. ") == ["Hold the rope.", "Cross the end over."]


def test_watch_without_a_key_says_nothing(c, scripted):
    sid = session(c)
    r = c.post(f"/api/sessions/{sid}/watch", files={"frame": ("f.jpg", JPEG, "image/jpeg")},
               data={"meta": json.dumps({"step_id": 4, "recent": [], "said": []})})
    assert r.status_code == 200 and r.json()["status"] == "working" and r.json()["say"] == ""
    bad = c.post(f"/api/sessions/{sid}/watch", files={"frame": ("f.jpg", b"nope", "image/jpeg")},
                 data={"meta": json.dumps({"step_id": 4})})
    assert bad.status_code == 400


def test_watch_live_sends_the_step_memory_and_both_pictures(c, meta):
    meta.handler = lambda r: httpx.Response(200, json={
        "output_text": json.dumps({"status": "mistake", "observation": "red wire going into Rh",
                                   "say": "Oh, wait, that's Rh. Pull it back out and push it into Rc."}),
        "usage": {"input_tokens": 900, "output_tokens": 40}})
    sid = session(c)
    r = c.post(f"/api/sessions/{sid}/watch", files={"frame": ("f.jpg", JPEG, "image/jpeg")},
               data={"meta": json.dumps({"step_id": 4, "recent": ["stripping the red wire"], "said": ["Good, keep going."],
                                         "elapsed_s": 31, "encourage": False})})
    assert r.json()["status"] == "mistake" and r.json()["say"].startswith("Oh, wait")
    sent = json.loads(meta.seen[0].content)
    text = sent["input"][0]["content"][0]["text"]
    assert "- stripping the red wire" in text and "- Good, keep going." in text and "Seconds since this step started: 31" in text
    assert sent["text"]["format"]["name"] == "watch" and len([x for x in sent["input"][0]["content"] if x["type"] == "input_image"]) == 2
