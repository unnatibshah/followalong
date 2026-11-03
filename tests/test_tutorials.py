"""Tutorial processing: UT-40-49 (step plan), UT-50-56 (uploads), the pipeline with a mocked gateway, the upload
route's real path, cancel and the cap refusal; one opt-in real run (`pytest -m live`, ~$0.03)."""

import asyncio
import io
import json
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import make_video
from server import providers
from server import tutorials as t
from server.gateway import Budget, GatewayError, gateway
from server.main import app


def raw_step(start, end, **kw):
    s = {"start": start, "end": end, "instruction": "Put the red wire in Rh", "done_looks_like": "Red wire under Rh",
         "objects": [{"name": "red wire", "sam_prompt": "red wire", "size": None},
                     {"name": "Rh terminal", "sam_prompt": "Rh terminal", "size": None}],
         "checks": [{"kind": "measured", "type": "inside", "a": "red wire", "b": "Rh terminal", "n": None, "side": None,
                     "text": "The red wire is in Rh", "confidence": 0.9},
                    {"kind": "judged", "type": "judged", "a": None, "b": None, "n": None, "side": None,
                     "text": "The screw is tight.", "confidence": 0.59}],
         "common_mistakes": ["a", "b", "c", "d"], "key_clip_start": start + 1, "key_clip_end": start + 4}
    s.update(kw)
    return s


def errors_of(raw, duration=60.0):
    with pytest.raises(t.PlanInvalid) as e:
        t.clean_plan(raw, duration)
    return " | ".join(e.value.errors)


# ---- UT-40-49: the step plan ----

def test_ut40_45_47_49_valid_plan_gets_code_fields():
    raw = {"steps": [raw_step(0, 20, id=99, end_frame="/etc/passwd"), raw_step(20, 40)]}
    steps = t.clean_plan(raw, 60.0)
    assert [s["id"] for s in steps] == [1, 2] and steps[0]["end_frame"] == "frames/step-1.jpg"  # model's id/path ignored
    assert [c["id"] for c in steps[1]["checks"]] == ["s2c1", "s2c2"]
    assert [c["needs_review"] for c in steps[0]["checks"]] == [False, True]  # 0.9 / 0.59 against 0.6
    assert steps[0]["checks"][1]["text"] == "The screw is tight"  # the UI adds its own punctuation
    assert len(steps[0]["common_mistakes"]) == 3
    assert steps[0]["checks"][1]["a"] is None and steps[0]["checks"][0]["ratio"] is None


def test_ut40_missing_field_rejected():
    assert "done_looks_like is missing" in errors_of({"steps": [raw_step(0, 20, done_looks_like="")]})
    assert "steps list" in errors_of({"plan": []})
    with pytest.raises(t.PlanInvalid) as e:
        t.clean_plan({"steps": []}, 60)
    assert e.value.empty


def test_ut48_disallowed_check_type_rejected():
    s = raw_step(0, 20)
    s["checks"][0]["type"] = "touching"
    assert "type 'touching' isn't allowed" in errors_of({"steps": [s]})
    s = raw_step(0, 20)
    s["checks"][0].update(type="inside", b="green wire")  # not one of the step's objects
    assert "b='green wire'" in errors_of({"steps": [s]})
    s = raw_step(0, 20)
    s["checks"][0].update(type="adjacent", side="near")
    assert "adjacent needs side" in errors_of({"steps": [s]})
    s = raw_step(0, 20)
    s["checks"][1]["kind"] = "measured"  # type judged with kind measured
    assert "judged check needs kind and type" in errors_of({"steps": [s]})


def test_ut49_sam_prompt_one_concept():
    s = raw_step(0, 20)
    s["objects"][0]["sam_prompt"] = "red wire, screwdriver"
    assert "one concept" in errors_of({"steps": [s]})
    s = raw_step(0, 20)
    s["objects"][0]["sam_prompt"] = "red wire and blue wire"
    assert "one concept" in errors_of({"steps": [s]})
    s = raw_step(0, 20)
    s["objects"][0]["sam_prompt"] = "large red wire"  # repaired: the size moves to `size`
    o = t.clean_plan({"steps": [s]}, 60)[0]["objects"][0]
    assert o["sam_prompt"] == "red wire" and o["size"] == "large"
    s = raw_step(0, 20)
    s["objects"][1]["name"] = "Red wire"
    assert "used twice" in errors_of({"steps": [s]})


def test_ut43_times_clamped_and_overlaps_removed():
    # sorted by start: (0,30), (25->30, 60 clamped), (40->60, 50): the last has no time left -> rejected
    assert "no time of its own" in errors_of({"steps": [raw_step(25, 70), raw_step(0, 30), raw_step(40, 50)]})
    steps = t.clean_plan({"steps": [raw_step(10, 32), raw_step(30, 90)]}, 60.0)
    assert [(s["start"], s["end"]) for s in steps] == [(10, 32), (32, 60)]


def test_ut44_key_clip_rules():
    steps = t.clean_plan({"steps": [raw_step(0, 20, key_clip_start=50, key_clip_end=53),  # outside: last 3 s
                                    raw_step(20, 22),                                       # 2 s step: whole step
                                    raw_step(22, 40, key_clip_start=39, key_clip_end=42)]}, 60.0)  # runs over: moved in
    assert [(s["key_clip_start"], s["key_clip_end"]) for s in steps] == [(17, 20), (20, 22), (37, 40)]


def test_done_at_rules():
    """The end frame's second: the model's done_at when inside the step, else 0.1 s before the step ends."""
    steps = t.clean_plan({"steps": [raw_step(0, 20, done_at=14.5),   # inside: kept
                                    raw_step(20, 30, done_at=35),    # after the step: just before its end
                                    raw_step(30, 40)]}, 60.0)        # missing: just before its end
    assert [s["done_at"] for s in steps] == [14.5, 29.9, 39.9]


def test_needs_from_objects_with_counts_without_terminals():
    s1 = raw_step(0, 20)
    s2 = raw_step(20, 40, objects=[{"name": "wire", "sam_prompt": "wire", "size": None},
                                   {"name": "red wire", "sam_prompt": "red wire", "size": None}],
                  checks=[{"kind": "measured", "type": "count", "a": "wire", "b": None, "n": 5, "side": None,
                           "text": "Five wires", "confidence": 0.8}])
    steps = t.clean_plan({"steps": [s1, s2]}, 60.0)
    assert t.needs(steps) == ["red wire", "wire ×5"]  # "Rh terminal" is part of the backplate
    plan = t.build_plan("x", steps)
    assert plan["model"] == "muse-spark-1.3" and plan["transcript_used"] is False
    assert plan["prompt_version"] == "step_plan-0.7" and plan["plan_hash"].startswith("sha256:")


# ---- UT-50-53: upload checks and conversion ----

MP4_BANNER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x':
  Metadata:
    major_brand     : isom
  Duration: 00:10:00.00, start: 0.000000, bitrate: 226 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1280x720
  Stream #0:1[0x2](und): Audio: aac (LC), 48000 Hz"""


def test_parse_probe_formats():
    assert t.parse_probe(MP4_BANNER) == {"format": "mp4", "duration": 600.0, "video": "h264", "audio": "aac"}
    assert t.parse_probe(MP4_BANNER.replace("isom", "qt"))["format"] == "mov"
    assert t.parse_probe("Input #0, matroska,webm, from 'x':\n  Duration: 00:00:05.50, start\n"
                         "  Stream #0:0: Video: vp9")["format"] == "webm"
    assert t.parse_probe("x.mp4: Invalid data found when processing input")["format"] is None


def test_ut50_52_check_upload():
    info = t.parse_probe(MP4_BANNER)
    t.check_upload(info, 600, True)  # exactly 10:00 locally is accepted
    with pytest.raises(t.Rejected) as e:
        t.check_upload({**info, "duration": 660.0}, 600, True)
    assert e.value.code == "too_long" and "11:00" in e.value.message
    with pytest.raises(t.Rejected) as e:
        t.check_upload({**info, "duration": 240.0}, 180, True)
    assert e.value.code == "too_long" and "on this demo" in e.value.message
    mov = {**info, "format": "mov"}
    t.check_upload(mov, 600, True)
    with pytest.raises(t.Rejected, match="readable MP4"):
        t.check_upload(mov, 600, False)  # FA_CONVERT_VIDEO=false: MP4 only (SIT-02 d)
    with pytest.raises(t.Rejected):
        t.check_upload({"format": "mp4", "duration": 3.0, "video": None, "audio": "aac"}, 600, True)  # audio only


def test_convert_command():
    info = t.parse_probe(MP4_BANNER)
    remux = t.convert_args(Path("in"), Path("out.mp4"), info)
    assert remux[remux.index("-c") + 1] == "copy" and "+faststart" in remux
    for other in ({**info, "format": "mov"}, {**info, "video": "hevc"}, {"format": "webm", "video": "vp9", "audio": "opus"}):
        args = t.convert_args(Path("in"), Path("out.mp4"), other)
        assert args[args.index("-c:v") + 1] == "libx264" and args[args.index("-c:a") + 1] == "aac"
        assert "+faststart" in args and args[-1] == "out.mp4"
    frame = t.frame_args(Path("v.mp4"), 19.9, Path("f.jpg"))
    assert frame[frame.index("-ss") + 1] == "19.900" and frame.index("-ss") < frame.index("-i")


def test_webm_converted_for_real(tmp_path):
    src = tmp_path / "in.webm"
    src.write_bytes(make_video(1, "webm"))
    info = asyncio.run(t.probe(src))
    assert info["format"] == "webm"
    asyncio.run(t.to_mp4(src, tmp_path / "out.mp4", info))
    out = asyncio.run(t.probe(tmp_path / "out.mp4"))
    assert out["format"] == "mp4" and out["video"] == "h264" and abs(out["duration"] - 1) < 0.3


# ---- the pipeline, gateway mocked ----

class FakeMeta:
    """Stands in for gateway.responses / upload_file / delete_file and sam.segment."""

    def __init__(self, answers, structured_400=False):
        self.answers, self.calls, self.deleted, self.uploads, self.structured_400 = list(answers), [], [], [], structured_400

    async def responses(self, body, timeout_s, session_id=None):
        self.calls.append(body)
        if self.structured_400 and "text" in body:
            raise GatewayError("http_error", 400)
        usage = {"input_tokens": 9000, "output_tokens": 2000, "video_frames_processed": 60}
        return {"output_text": json.dumps(self.answers.pop(0)), "usage": usage}, 0.02

    async def transcribe(self, wav, seconds, session_id=None, mode="PUSH_TO_TALK", timeout_s=None):
        self.transcribed = getattr(self, "transcribed", []) + [(wav, seconds, mode)]
        return {"transcript": "Loosen the screw. Push the wire in.", "audioDurationMs": int(seconds * 1000),
                "turns": [{"turnId": 1, "startMs": 500, "endMs": 1200, "transcript": "Loosen the screw."},
                          {"turnId": 2, "startMs": 1300, "endMs": 1900, "transcript": "Push the wire  in."}]}, 0.0001

    async def upload_file(self, path, ttl_s=86400):
        self.uploads.append((Path(path).name, ttl_s))
        return "file-1"

    async def delete_file(self, fid):
        self.deleted.append(fid)

    async def segment(self, frame, concept, session_id=None):
        mask = np.zeros((36, 64), bool)
        mask[5:7, 10:12] = True  # 0.17% of the frame: inside the terminal size band
        n = 6 if concept == "circle" else 1
        return {"concept": concept, "size": (64, 36), "latency_ms": 5, "cost_usd": 0.0025,
                "instances": [{"box": [10 + i, 5, 29 + i, 14], "score": 0.9, "mask": mask} for i in range(n)]}


@pytest.fixture
def fake(monkeypatch):
    def install(answers, **kw):
        f = FakeMeta(answers, **kw)
        for name in ("responses", "upload_file", "delete_file", "transcribe"):
            monkeypatch.setattr(gateway, name, getattr(f, name))
        monkeypatch.setattr(t.sam, "segment", f.segment)
        return f
    return install


GOOD = {"steps": [raw_step(0.0, 1.0), raw_step(1.0, 2.0, checks=[
    {"kind": "measured", "type": "area_ratio", "a": "red wire", "b": "Rh terminal", "n": None, "side": None,
     "text": "Same size", "confidence": 0.8}])]}
BAD = {"steps": [raw_step(0.0, 1.0, checks=[{"kind": "measured", "type": "touching", "a": "red wire", "b": None,
                                             "n": None, "side": None, "text": "x", "confidence": 0.9}])]}


def test_pipeline_end_to_end(fake, tmp_path, mp4):
    f = fake([GOOD])
    src = tmp_path / "source"
    src.write_bytes(mp4)
    seen = []
    plan, stats = asyncio.run(t.process(src, tmp_path / "out", "abc", asyncio.run(t.probe(src)), seen.append))
    assert seen[0] == "finding_steps 0/0" and seen[1] == "outlining 0/2" and sorted(seen[2:]) == ["outlining 1/2", "outlining 2/2"]
    assert [s["id"] for s in plan["steps"]] == [1, 2] and plan["tutorial_id"] == "abc"
    for n in (1, 2):
        assert (tmp_path / "out" / "frames" / f"step-{n}.jpg").read_bytes()[:3] == b"\xff\xd8\xff"
    refs = json.loads((tmp_path / "out" / "refs" / "step-1.json").read_text(encoding="utf-8"))
    assert refs["frame_size"] == [64, 36] and all(o["found"] for o in refs["objects"])  # Rh = 2nd of 6 terminals
    assert plan["steps"][1]["checks"][0]["ratio"] == 1.0
    assert f.uploads == [("video.mp4", 86400)] and f.deleted == ["file-1"]  # UT-55
    body = f.calls[0]
    assert body["model"] == "muse-spark-1.3" and body["text"]["format"]["name"] == "step_plan"
    assert body["input"][0]["content"][1] == {"type": "input_file", "file_id": "file-1"}
    assert stats["video_frames_processed"] == 60 and set(stats["stages_ms"]) >= {"convert", "muse_spark", "sam"}


def test_def017_outlines_one_step_at_a_time_and_a_dead_sam_fails_the_upload(fake, tmp_path, mp4, monkeypatch):
    f = fake([GOOD, GOOD])
    src = tmp_path / "source"
    src.write_bytes(mp4)
    running, peak = 0, []
    real = f.segment

    async def counting(frame, concept, session_id=None):
        nonlocal running
        running += 1
        peak.append(running)
        await asyncio.sleep(0.01)
        running -= 1
        return await real(frame, concept, session_id)
    monkeypatch.setattr(t.sam, "segment", counting)
    asyncio.run(t.process(src, tmp_path / "out", "abc", asyncio.run(t.probe(src))))
    assert max(peak) <= 2  # one step's prompts (red wire + the terminal prompt), never every step's at once

    async def down(frame, concept, session_id=None):
        raise GatewayError("failed")
    monkeypatch.setattr(t.sam, "segment", down)
    src.write_bytes(mp4)
    with pytest.raises(t.Failed, match="outline"):
        asyncio.run(t.process(src, tmp_path / "out2", "abc", asyncio.run(t.probe(src))))


def test_ut41_one_retry_with_the_errors(fake):
    f = fake([BAD, GOOD])
    steps = asyncio.run(t.find_steps("file-1", 2.0, stats := {}))
    assert len(steps) == 2 and len(f.calls) == 2 and stats["calls"] == 2
    assert "touching" in f.calls[1]["input"][0]["content"][-1]["text"]


def test_ut42_invalid_twice_fails_and_still_deletes_the_file(fake, tmp_path, mp4):
    f = fake([BAD, BAD])
    src = tmp_path / "source"
    src.write_bytes(mp4)
    with pytest.raises(t.Failed):
        asyncio.run(t.process(src, tmp_path / "out", "abc", asyncio.run(t.probe(src))))
    assert len(f.calls) == 2 and f.deleted == ["file-1"]


def test_structured_output_refused_falls_back_to_prompted_json(fake):
    f = fake([GOOD], structured_400=True)
    stats = {}
    asyncio.run(t.find_steps("file-1", 2.0, stats))
    assert "text" in f.calls[0] and "text" not in f.calls[1] and "JSON Schema" in f.calls[1]["input"][0]["content"][-1]["text"]
    assert stats["structured_output"] is False


def test_not_a_tutorial(fake):
    fake([{"steps": []}])
    with pytest.raises(t.Failed, match="hands-on"):
        asyncio.run(t.find_steps("file-1", 2.0, {}))


# ---- the upload route ----

def wait_for(c, tid, status, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        meta = c.get(f"/api/tutorials/{tid}").json()
        if meta.get("status") == status:
            return meta
        time.sleep(0.05)
    raise AssertionError(f"{tid} never reached {status}: {meta}")


def test_ut52_53_text_or_url_is_invalid_upload(scripted):
    c = TestClient(app)
    for body in (b"not a video at all", b"https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
        r = c.post("/api/tutorials", files={"file": ("x.mp4", body, "video/mp4")})
        assert r.status_code == 415 and r.json()["error"]["code"] == "invalid_upload"
    r = c.post("/api/tutorials", data={"file": "https://example.com/video.mp4"})  # DEF-005: a URL as the field
    assert r.status_code == 415 and r.json()["error"]["code"] == "invalid_upload"


def test_ut50_too_long_through_the_route(monkeypatch, mp4, scripted):
    monkeypatch.setattr(providers, "UPLOAD_MAX_S", 1)
    r = TestClient(app).post("/api/tutorials", files={"file": ("x.mp4", mp4, "video/mp4")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_long" and "0:02" in r.json()["error"]["message"]


def test_scripted_fallback_is_the_default_plan(mp4, scripted):
    c = TestClient(app)
    tid = c.post("/api/tutorials", files={"file": ("mine.mp4", mp4, "video/mp4")}).json()["id"]
    assert providers.tutorials.uploads[tid]["real"] is False
    assert c.get(f"/api/tutorials/{tid}").json()["duration_s"] == 2.0  # measured by ffmpeg
    providers.tutorials.uploads[tid]["t0"] -= 10
    plan = c.get(f"/api/tutorials/{tid}/plan").json()
    assert plan["tutorial_id"] == tid and len(plan["steps"]) == 7  # the default (thermostat) tutorial's


def test_real_upload_progress_ready_and_media(monkeypatch, fake, mp4):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    fake([GOOD])
    with TestClient(app) as c:  # one event loop for the whole test, so the background task lives
        r = c.post("/api/tutorials", files={"file": ("wiring.mp4", mp4, "video/mp4")})
        assert r.status_code == 202
        tid = r.json()["id"]
        meta = wait_for(c, tid, "ready")
        assert meta["step_count"] == 2 and meta["progress"] is None and meta.pop("safety") is not None and meta.keys() | {"source_url", "source_title"} == providers.FixtureTutorials().bundled["install-thermostat"].keys()
        plan = c.get(f"/api/tutorials/{tid}/plan").json()
        assert plan["tutorial_id"] == tid and plan["needs"] == ["red wire"]
        assert c.get(f"/api/tutorials/{tid}/video").content[4:8] == b"ftyp"
        assert c.get(f"/api/tutorials/{tid}/frames/2").content == (providers.RUNTIME / "tutorials" / tid / "frames" / "step-2.jpg").read_bytes()
        assert not (providers.RUNTIME / "tutorials" / tid / "source").exists()
        assert c.delete(f"/api/tutorials/{tid}").status_code == 204


def test_cancel_stops_the_work(monkeypatch, mp4):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    cancelled = []

    async def slow(src, out, tid, info, progress):
        progress("finding_steps 0/0")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(tid)
            raise
    monkeypatch.setattr(providers.processing, "process", slow)
    with TestClient(app) as c:
        tid = c.post("/api/tutorials", files={"file": ("w.mp4", mp4, "video/mp4")}).json()["id"]
        assert c.get(f"/api/tutorials/{tid}").json()["progress"] == "finding_steps 0/0"
        r = c.post("/api/tutorials", files={"file": ("again.mp4", mp4, "video/mp4")})  # one job per visitor
        assert r.status_code == 429 and "still being processed" in r.json()["error"]["message"]
        assert c.delete(f"/api/tutorials/{tid}").status_code == 204
        for _ in range(50):
            if cancelled:
                break
            time.sleep(0.02)
        assert cancelled == [tid] and not (providers.RUNTIME / "tutorials" / tid).exists()
        assert c.get(f"/api/tutorials/{tid}").status_code == 404


def test_failure_keeps_only_the_status(monkeypatch, fake, mp4):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    fake([BAD, BAD])
    with TestClient(app) as c:
        tid = c.post("/api/tutorials", files={"file": ("w.mp4", mp4, "video/mp4")}).json()["id"]
        wait_for(c, tid, "failed")
        d = providers.RUNTIME / "tutorials" / tid
        assert sorted(p.name for p in d.iterdir()) == ["meta.json"]
        assert c.get(f"/api/tutorials/{tid}/plan").status_code == 404


def test_cap_refusal_before_processing(monkeypatch, tmp_path, mp4, scripted):
    monkeypatch.setattr(gateway, "budget", Budget(tmp_path / "b.json", {"session": 1.5, "day": 0.01, "total": 50}, True))
    before = set((providers.RUNTIME / "tutorials").iterdir())
    r = TestClient(app).post("/api/tutorials", files={"file": ("w.mp4", mp4, "video/mp4")})
    assert r.status_code == 429 and r.json()["error"]["code"] == "cap_reached" and r.json()["error"]["retry_after_s"] > 0
    assert set((providers.RUNTIME / "tutorials").iterdir()) == before  # nothing kept


def test_ut54_hosted_cleanup_after_24h(monkeypatch, mp4, scripted):
    c = TestClient(app)
    tid = c.post("/api/tutorials", files={"file": ("w.mp4", mp4, "video/mp4")}).json()["id"]
    monkeypatch.setattr(providers, "HOSTED", True)
    providers.tutorials.uploads[tid]["meta"]["delete_after"] = "2000-01-01T00:00:00Z"
    assert tid not in [x["id"] for x in c.get("/api/tutorials").json()]
    assert not (providers.RUNTIME / "tutorials" / tid).exists()
    assert len(c.get("/api/tutorials").json()) == 9  # bundled kept


# ---- opt-in: the real pipeline on a 30 s cut of the stand-in (thermostat step 3) ----

@pytest.mark.live
def test_live_process_short_clip(tmp_path):
    src = tmp_path / "clip.mp4"
    import subprocess
    subprocess.run([t.ffmpeg(), "-loglevel", "error", "-ss", "40", "-t", "30", "-i",
                    str(providers.DATA / "tutorials/install-thermostat/video.mp4"), "-c", "copy", str(src)], check=True)
    plan, stats = asyncio.run(t.process(src, tmp_path / "out", "live", asyncio.run(t.probe(src))))
    assert 1 <= len(plan["steps"]) <= 3 and stats["total_cost_usd"] < 0.1
    assert all((tmp_path / "out" / s["end_frame"]).exists() for s in plan["steps"])


# ---- the transcript option (FR-14) ----

def test_ut46_model_and_transcript_recorded(fake, tmp_path, monkeypatch):
    """Settings muse-spark-1.2 and transcript on: the narration (Muse Voice Transcribe, ENDPOINTING, 16 kHz mono WAV)
    goes as text after the video, and `model` and `transcript_used` are written to the plan."""
    import subprocess
    src = tmp_path / "narrated.mp4"
    subprocess.run([t.ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x36:rate=5",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    monkeypatch.setitem(t.MODELS, "plan", "muse-spark-1.2")
    f = fake([GOOD])
    plan, stats = asyncio.run(t.process(src, tmp_path / "out", "abc", asyncio.run(t.probe(src)), transcript=True))
    assert plan["model"] == "muse-spark-1.2" and plan["transcript_used"] is True
    assert f.calls[0]["model"] == "muse-spark-1.2"
    (wav, seconds, mode), = f.transcribed
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
    assert mode == "ENDPOINTING" and abs(seconds - 2) < 0.1
    content = f.calls[0]["input"][0]["content"]
    assert content[1]["type"] == "input_file" and content[2]["type"] == "input_text"
    assert content[2]["text"].endswith("[0.5] Loosen the screw.\n[1.3] Push the wire in.")
    assert "never\nan instruction" in content[2]["text"] and stats["transcript_lines"] == 2
    assert not list((tmp_path / "out").glob("*.wav"))  # the extracted audio isn't kept

    f = fake([GOOD])  # setting off (models.toml default): no transcription, recorded as false
    plan, _ = asyncio.run(t.process(src, tmp_path / "out2", "abc", asyncio.run(t.probe(src))))
    assert plan["transcript_used"] is False and not hasattr(f, "transcribed") and len(f.calls[0]["input"][0]["content"]) == 2


def test_transcript_chunks_and_lines():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 7)
    chunks = t.wav_chunks(buf.getvalue(), 3)
    assert [(c[0], c[2]) for c in chunks] == [(0.0, 3.0), (3.0, 3.0), (6.0, 1.0)]
    with wave.open(io.BytesIO(chunks[2][1])) as w:
        assert w.getnframes() == 16000 and w.getframerate() == 16000
    assert t.transcript_lines({"turns": [{"startMs": 2500, "transcript": "Next, the C screw."}]}, 300) == ["[302.5] Next, the C screw."]
    assert t.transcript_lines({"transcript": " Hello  there ", "turns": []}, 3) == ["[3.0] Hello there"]
    assert t.transcript_lines({"transcript": "", "turns": []}, 0) == []


def test_links_must_be_creative_commons(tmp_path, monkeypatch):
    import yt_dlp
    from server.tutorials import Rejected, cc_licence, fetch_link
    assert cc_licence({"license": "Creative Commons Attribution license (reuse allowed)"})
    for lic in ("by", "by-sa", "cc0", "CC BY-NC"):
        assert cc_licence({"license": lic}), lic
    for lic in (None, "", "Standard YouTube License", "bypass", "all rights reserved"):
        assert not cc_licence({"license": lic}), lic

    class Y:  # a standard-licence video: refused before anything downloads
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): return {"duration": 60, "license": None}
        def download(self, urls): raise AssertionError("downloaded a video we may not use")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Y)
    with pytest.raises(Rejected, match="Creative Commons") as e:
        fetch_link("https://www.youtube.com/watch?v=VdnkRQF5Cps", 600, tmp_path)
    assert e.value.code == "not_licensed"


def test_links_only_https_on_video_hosts():
    from server.tutorials import link_ok
    assert link_ok("https://www.youtube.com/watch?v=VdnkRQF5Cps") and link_ok("https://youtu.be/VdnkRQF5Cps")
    assert link_ok("https://vimeo.com/123")
    for bad in ("http://www.youtube.com/watch?v=x", "https://evil.example/video.mp4", "https://169.254.169.254/latest",
                "https://user:pw@youtube.com/x", "file:///etc/passwd", "https://youtube.com.evil.example/x"):
        assert not link_ok(bad), bad
