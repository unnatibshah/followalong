"""Manual to tutorial (CR-006): UT-130–137, plus the whole upload → ready → media path with the gateway mocked."""

import asyncio
import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import manual as m
from server import providers, safety
from server import tutorials as t
from server.main import app


def page(color="white", size=(850, 1100)) -> Image.Image:
    im = Image.new("RGB", size, color)
    im.paste((40, 40, 40), (200, 300, 650, 700))  # a "figure"
    return im


def make_pdf(path: Path, n: int, extra: bytes = b"") -> Path:
    ims = [page() for _ in range(n)]
    ims[0].save(path, "PDF", save_all=True, append_images=ims[1:])
    if extra:
        path.write_bytes(path.read_bytes() + extra)
    return path


def png(color="white") -> bytes:
    buf = io.BytesIO()
    page(color).save(buf, "PNG")
    return buf.getvalue()


def raw_step(page_no, instruction, box=(100, 200, 800, 700), warning="", checks=None):
    return {"page": page_no, "figure_box": list(box), "instruction": instruction,
            "done_looks_like": "The legs stand upright under the seat.",
            "objects": [{"name": "wooden leg", "sam_prompt": "wooden leg", "size": None},
                        {"name": "seat", "sam_prompt": "round seat", "size": None}],
            "checks": checks if checks is not None else [
                {"kind": "measured", "type": "count", "a": "wooden leg", "b": None, "n": 3, "side": None,
                 "text": "Three legs", "confidence": 0.9},
                {"kind": "judged", "type": "judged", "a": None, "b": None, "n": None, "side": None,
                 "text": "The screws are flush with the leg brackets", "confidence": 0.8}],
            "common_mistakes": ["a leg turned the wrong way"], "hands_ok": False,
            "parts_used": [{"name": "screw", "count": 6}], "warning": warning}


GOOD = {"title": "Three-leg stool", "parts": [{"name": "screw", "count": 6}, {"name": "glide", "count": 3}],
        "steps": [raw_step(2, "Screw the 3 legs to the underside of the seat with 6 screws"),
                  raw_step(3, "Press a glide into the foot of each leg", warning="Small parts: keep them from children.")]}


# ---- UT-130: accept and reject (FR-160) ----

def check(path):
    return asyncio.run(m.check_pdf(path))


def test_ut130_pdf_limits(tmp_path, monkeypatch):
    assert check(make_pdf(tmp_path / "ok.pdf", 3)) == 3
    with pytest.raises(m.Rejected) as e:
        check(make_pdf(tmp_path / "long.pdf", 41))
    assert e.value.code == "too_many_pages" and "41 pages" in e.value.message
    with pytest.raises(m.Rejected) as e:
        check(make_pdf(tmp_path / "js.pdf", 1, b"\n1 0 obj << /S /JavaScript /JS (app.alert(1)) >> endobj\n"))
    assert e.value.code == "invalid_manual"
    with pytest.raises(m.Rejected) as e:
        check(make_pdf(tmp_path / "att.pdf", 1, b"\n<< /Type /EmbeddedFile >>\n"))
    assert e.value.code == "invalid_manual"
    (tmp_path / "fake.pdf").write_bytes(b"hello")
    with pytest.raises(m.Rejected) as e:
        check(tmp_path / "fake.pdf")
    assert e.value.code == "invalid_upload"
    monkeypatch.setattr(m, "MAX_PDF_MB", 0)
    with pytest.raises(m.Rejected, match="over 0 MB"):
        check(tmp_path / "ok.pdf")


def test_ut130_chance_bytes_are_not_scripts(tmp_path):
    # a short name like /JS turns up by chance inside compressed image data: only PDFium's own answer refuses it
    assert check(make_pdf(tmp_path / "ok.pdf", 1, b"\n% image bytes /JS) /XFA]\n")) == 1


def test_ut130_locked_pdf(tmp_path, monkeypatch):
    import pypdfium2 as pdfium

    def locked(*a, **k):
        raise pdfium.PdfiumError("Failed to load document (PDFium: Incorrect password error).")
    monkeypatch.setattr(pdfium, "PdfDocument", locked)
    assert m.probe_pdf(tmp_path / "x.pdf") == {"locked": True}  # the probe (it runs in a subprocess)

    async def probe(args, timeout):
        return 0, '{"locked": true}\n'
    monkeypatch.setattr(t, "_run", probe)
    with pytest.raises(m.Rejected) as e:
        check(make_pdf(tmp_path / "x.pdf", 1))
    assert e.value.code == "invalid_manual" and "locked" in e.value.message

    async def hung(args, timeout):
        raise TimeoutError
    monkeypatch.setattr(t, "_run", hung)
    with pytest.raises(m.Rejected, match="couldn't read"):
        check(make_pdf(tmp_path / "x.pdf", 1))


def test_ut130_images(tmp_path):
    paths = []
    for i in range(3):
        (tmp_path / f"{i}.png").write_bytes(png())
        paths.append(tmp_path / f"{i}.png")
    assert m.check_images(paths) == 3
    with pytest.raises(m.Rejected) as e:
        m.check_images(paths * 7)
    assert e.value.code == "too_many_pages"
    (tmp_path / "bad.png").write_bytes(b"not an image")
    with pytest.raises(m.Rejected) as e:
        m.check_images([tmp_path / "bad.png"])
    assert e.value.code == "invalid_upload"
    Image.new("RGB", (10, 10)).save(tmp_path / "x.gif", "GIF")
    with pytest.raises(m.Rejected, match="JPEG, PNG or WebP"):
        m.check_images([tmp_path / "x.gif"])


# ---- UT-131: the request (FR-161) ----

def test_ut131_request_and_retry(fake, tmp_path):
    for i, color in enumerate(("white", "lightgrey"), 1):
        page(color).save(tmp_path / f"{i}.png")
    pages = m.pages_from_images([tmp_path / "1.png", tmp_path / "2.png"], tmp_path / "pages")
    bad = {**GOOD, "steps": [raw_step(1, "x", checks=[{"kind": "measured", "type": "touching", "a": "seat", "b": None,
                                                         "n": None, "side": None, "text": "x", "confidence": 1}])]}
    f = fake([bad, {**GOOD, "steps": GOOD["steps"][:1]}])
    info, steps = asyncio.run(m.find_steps(pages, {}))
    body = f.calls[0]
    assert body["model"] == "muse-spark-1.3" and body["text"]["format"]["name"] == "manual_plan"
    content = body["input"][0]["content"]
    assert [c["text"] for c in content if c["type"] == "input_text"][1:] == ["Page 1:", "Page 2:"]
    assert sum(c["type"] == "input_image" for c in content) == 2
    assert "touching" in f.calls[1]["input"][0]["content"][-1]["text"]  # the retry carries the errors
    assert info["title"] == "Three-leg stool" and len(steps) == 1


def test_ut131_body_shrinks_to_fit(tmp_path, monkeypatch):
    page("white").save(tmp_path / "1.png")
    big = m.plan_request([tmp_path / "1.png"])
    monkeypatch.setattr(m, "MAX_BODY_MB", len(json.dumps(big)) / 1024 / 1024 - 0.001)  # the first size no longer fits
    small = m.plan_request([tmp_path / "1.png"])
    assert len(json.dumps(small)) < len(json.dumps(big))


# ---- UT-132: cleaning (FR-162) ----

def test_ut132_clean(monkeypatch):
    raw = {"title": " Stool ", "parts": [{"name": "screw", "count": 0}],
           "steps": [raw_step(0, "A", box=(-50, 2000, 300, 100)), raw_step(9, "B", box=(10, 10, 20, 20)),
                     raw_step(3, "C", box=[1, 2]), raw_step(3, "C"), raw_step(3, "  ")]}
    info, steps = m.clean_manual(raw, 8)
    assert [s["manual_page"] for s in steps] == [1, 8, 3]
    assert steps[0]["figure_box"] == [0, 100, 300, 1000]
    assert steps[1]["figure_box"] == steps[2]["figure_box"] == [0, 0, 1000, 1000]  # tiny, malformed
    assert info == {"title": "Stool", "parts": [{"name": "screw", "count": 1}]}
    many = {"title": "", "parts": [], "steps": [raw_step(1, f"Step {i}") for i in range(45)]}
    assert len(m.clean_manual(many, 1)[1]) == 40
    with pytest.raises(t.PlanInvalid) as e:
        m.clean_manual({"title": "", "parts": [], "steps": []}, 1)
    assert e.value.empty


# ---- UT-133, UT-134: schedule and the animated video (FR-163) ----

def test_ut133_schedule():
    from server import animate
    assert [animate.seconds(" ".join(["w"] * n)) for n in (3, 10, 40)] == [9.0, 10.0, 16.0]
    _, steps = m.clean_manual({"title": "", "parts": [], "steps": [raw_step(1, "Do it"), raw_step(1, " ".join(["w"] * 10))]}, 1)
    assert (steps[0]["start"], steps[0]["end"], steps[1]["start"], steps[1]["end"]) == (0.0, 9.0, 9.0, 19.0)
    for s in steps:
        ph = animate.phases(s["end"] - s["start"])
        assert s["start"] + ph["hold"][0] <= s["done_at"] <= s["end"] - 0.1  # the end frame is in the final hold
        assert s["key_clip_start"] == pytest.approx(s["start"] + ph["focus"][0], abs=0.01)  # the loop clip is the close-up


def test_ut139_focus_box():
    step = lambda **k: {**raw_step(1, "Fit it", box=(100, 100, 900, 900)), **k}  # noqa: E731
    _, steps = m.clean_manual({"title": "", "parts": [], "steps": [
        step(focus_box=[400, 400, 450, 450]), step(instruction="b", focus_box=[0, 0, 50, 50]),
        step(instruction="c", focus_box="x")]}, 1)
    assert steps[0]["focus_box"] == [400, 400, 450, 450]
    assert steps[1]["focus_box"] is None and steps[2]["focus_box"] is None  # outside the figure · malformed


def test_ut134_animated_video(tmp_path, monkeypatch):
    from server import animate
    monkeypatch.setattr(animate, "FPS", 5)  # same schedule and checks, a quarter of the frames to encode
    page(size=(850, 1400)).save(tmp_path / "01.png")
    _, steps = m.clean_manual({"title": "", "parts": [], "steps": [
        {**raw_step(1, "Screw the legs on", box=(200, 250, 800, 520)), "focus_box": [400, 300, 500, 400]},
        raw_step(1, "Turn it over")]}, 1)
    with Image.open(tmp_path / "01.png") as p:
        st = animate.Step(p, steps[0], 2)
    ph = st.ph
    card, drawing, close, done = (Image.fromarray(st.frame(x)) for x in
                                  (0.8, (ph["draw"][0] + ph["draw"][1]) / 2, ph["focus"][1] - 0.1, ph["hold"][0] + 0.5))
    assert card.size == done.size == (1280, 720)
    assert done.getpixel((5, 715)) != (255, 255, 255)  # the caption band
    dark = lambda im: sum(1 for v in im.crop((0, 0, 1280, 570)).convert("L").get_flattened_data() if v < 128)  # noqa: E731
    assert 0 < dark(drawing) < dark(done)  # the figure is drawing itself on
    assert dark(close) > dark(done)  # zoomed in: the figure fills more of the picture
    seconds = asyncio.run(m.build_video([tmp_path / "01.png"], steps, tmp_path / "out"))
    assert seconds == steps[-1]["end"]
    info = asyncio.run(t.probe(tmp_path / "out" / "video.mp4"))
    assert info["video"] == "h264" and abs(info["duration"] - seconds) < 0.2
    assert (tmp_path / "out" / "frames" / "step-2.jpg").exists()


def test_video_build_is_bounded(tmp_path, monkeypatch):
    """A build past its time budget stops (ffmpeg killed, no video), and any ffmpeg failure is the friendly Failed."""
    import subprocess

    from server import animate
    page().save(tmp_path / "01.png")
    _, steps = m.clean_manual({"title": "", "parts": [], "steps": [raw_step(1, "Fit it")]}, 1)
    with pytest.raises(RuntimeError, match="out of time"):
        animate.render([tmp_path / "01.png"], steps, tmp_path / "a", t.ffmpeg(), -10_000)
    assert not (tmp_path / "a" / "video.mp4").exists() or (tmp_path / "a" / "video.mp4").stat().st_size == 0

    def stuck(*a):
        raise subprocess.TimeoutExpired("ffmpeg", 1)
    monkeypatch.setattr(animate, "render", stuck)
    with pytest.raises(t.Failed, match="build the video"):
        asyncio.run(m.build_video([tmp_path / "01.png"], steps, tmp_path / "b"))


def test_render_pdf_in_a_subprocess(tmp_path):
    pages = asyncio.run(m.pages_from_pdf(make_pdf(tmp_path / "m.pdf", 2), tmp_path / "pages"))
    assert [p.name for p in pages] == ["01.png", "02.png"]


# ---- UT-135: hand-over to the video pipeline (FR-164, FR-166) ----

def test_ut135_process(fake, tmp_path):
    left_of = {"kind": "measured", "type": "left_of", "a": "wooden leg", "b": "seat", "n": None, "side": None,
               "text": "Leg left of the seat", "confidence": 0.9}
    answer = json.loads(json.dumps(GOOD))
    answer["steps"][0]["checks"].append(left_of)
    f = fake([answer])
    src = make_pdf(tmp_path / "m.pdf", 3)
    seen = []
    plan, stats = asyncio.run(m.process(src, "pdf", tmp_path / "out", "abc", seen.append))
    assert seen[0] == "finding_steps 0/0" and "outlining 2/2" in seen
    assert plan["source"] == "manual" and plan["manual"] == {"kind": "pdf", "pages": 3, "title": "Three-leg stool"}
    assert plan["needs"] == ["screw ×6", "glide ×3"] and plan["transcript_used"] is False
    s1 = plan["steps"][0]
    assert s1["manual_page"] == 2 and s1["parts_used"] == [{"name": "screw", "count": 6}] and s1["figure_box"]
    assert plan["steps"][1]["warning"].startswith("Small parts")
    assert (tmp_path / "out" / "refs" / "step-1.json").exists() and stats["duration_s"] > 0
    assert f.uploads == [] and f.calls[0]["text"]["format"]["name"] == "manual_plan"  # no video sent for the plan
    assert plan == t.rehash(dict(plan))
    by_type = {c["type"]: c for c in s1["checks"]}
    # FR-166: self-calibration on the figure switches off the measured check that fails on it (the fake SAM finds one
    # leg, not three); the judged check stays
    assert not by_type["count"]["enabled"] and by_type["judged"]["enabled"] and stats["calibration_disabled"] >= 1


# ---- UT-136: privacy over the API (FR-165), and the whole path ----

def wait_for(c, tid, status):
    for _ in range(1200):  # the video takes a few seconds to animate
        meta = c.get(f"/api/tutorials/{tid}").json()
        if meta["status"] == status:
            return meta
        time.sleep(0.05)
    raise AssertionError(meta)


def test_ut136_upload_ready_private_and_deleted(monkeypatch, fake, tmp_path):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    fake([GOOD])
    pdf = make_pdf(tmp_path / "stool.pdf", 3).read_bytes()
    with TestClient(app) as c:
        r = c.post("/api/tutorials", data={"kind": "manual"}, files={"files": ("stool.pdf", pdf, "application/pdf")})
        assert r.status_code == 202, r.text
        tid = r.json()["id"]
        meta = wait_for(c, tid, "ready")
        assert meta["source"] == "manual" and meta["manual"] == {"kind": "pdf", "pages": 3}
        assert meta["step_count"] == 2 and meta["duration_s"] > 0 and meta["title"] == "Three-leg stool"
        assert c.get(f"/api/tutorials/{tid}/video").content[4:8] == b"ftyp"
        assert c.get(f"/api/tutorials/{tid}/frames/1").content[:3] == b"\xff\xd8\xff"
        assert c.get(f"/api/tutorials/{tid}/plan").json()["steps"][0]["manual_page"] == 2
        det = c.get(f"/api/tutorials/{tid}/details").json()  # FR-168 without a directions pass
        assert det["2"]["safety"].startswith("Small parts") and det["2"]["hazard"] == "caution"
        assert not (providers.RUNTIME / "tutorials" / tid / "source.pdf").exists()
        again = c.post("/api/tutorials", data={"kind": "manual"}, files={"files": ("stool.pdf", pdf, "application/pdf")})
        assert again.status_code == 200 and again.json() == {"id": tid, "status": "ready", "duplicate": True}
    with TestClient(app) as other:  # another browser: no cookie
        assert other.get(f"/api/tutorials/{tid}").status_code == 404
        assert tid not in [x["id"] for x in other.get("/api/tutorials").json()]
    monkeypatch.setattr(providers, "HOSTED", True)
    providers.tutorials.uploads[tid]["meta"]["delete_after"] = "2000-01-01T00:00:00Z"
    providers.tutorials.purge()
    assert not (providers.RUNTIME / "tutorials" / tid).exists()


def test_manual_photos_and_refusals(monkeypatch, fake):
    monkeypatch.setenv("MODEL_API_KEY", "sk-test")
    fake([{**GOOD, "steps": [raw_step(1, "Screw the legs on"), raw_step(2, "Add the glides")]}])
    with TestClient(app) as c:
        r = c.post("/api/tutorials", data={"kind": "manual"},
                   files=[("files", ("p1.jpg", png("white"), "image/jpeg")), ("files", ("p2.png", png("ivory"), "image/png"))])
        assert r.status_code == 202, r.text
        meta = wait_for(c, r.json()["id"], "ready")
        assert meta["manual"] == {"kind": "images", "pages": 2} and meta["title"] == "Three-leg stool"
        many = [("files", (f"{i}.png", png(), "image/png")) for i in range(30)]
        r = c.post("/api/tutorials", data={"kind": "manual"}, files=many)
        assert r.status_code == 413 and r.json()["error"]["code"] == "too_many_pages"
        r = c.post("/api/tutorials", data={"kind": "manual"}, files={"files": ("notes.txt", b"hi", "text/plain")})
        assert r.status_code == 415 and r.json()["error"]["code"] == "invalid_upload"
        assert not [d for d in (providers.RUNTIME / "tutorials").iterdir() if not (d / "meta.json").exists()]


# ---- UT-137: warnings to safety tiers (FR-168) ----

def test_ut137_warnings():
    assert safety.tier("Anchor the shelf to the wall so it can't tip over.") == "danger"
    assert safety.tier("Two people are needed for this step.") == "danger"
    assert safety.tier("Unplug the lamp before you fit the bulb.") == "gate"
    assert safety.tier("") == ""
    assert "Warning: Small parts" in m.notes([{"start": 0, "manual_page": 3, "instruction": "Press the glides in.",
                                                "warning": "Small parts: keep them from children."}])


def test_drawing_checks_ask_and_say_drawing():
    from server import spark
    from server.checks import engine
    fail = {"pass": False, "type": "count"}
    assert engine.verdict(True, [fail], []) == "wrong"
    assert engine.verdict(True, [{**fail, "veto": False}], []) == "unsure"  # a manual step's measured fail asks
    step = {"id": 1, "instruction": "Fit the legs", "done_looks_like": "Legs on", "manual_page": 2}
    text = spark.request(step, [], "data:,", "data:,")["input"][0]["content"][0]["text"]
    assert "drawing from the instruction manual" in text
    assert spark.done_text({"done_looks_like": "x"}, "x") == "x"  # a video's step is unchanged


def test_ut130_huge_photo(tmp_path, monkeypatch):
    page().save(tmp_path / "big.png")
    monkeypatch.setattr(m, "MAX_PHOTO_PIXELS", 1000)
    with pytest.raises(m.Rejected, match="too large a picture"):
        m.check_images([tmp_path / "big.png"])


def test_snap_grows_a_short_box_to_the_figure_and_no_further():
    from PIL import ImageDraw
    pg = Image.new("L", (1000, 1000), 230)  # paper, 1 px = 1 unit
    d = ImageDraw.Draw(pg)
    d.rectangle((300, 200, 700, 600), outline=0, width=3)  # the step's drawing
    d.rectangle((300, 800, 700, 900), outline=0, width=3)  # the next step's drawing, past a blank band
    d.rectangle((0, 0, 1000, 60), fill=60)  # dark table along the top of a photo
    x0, y0, x1, y1 = m.snap(pg, [320, 220, 680, 510])  # the model's box stops ~9% short, as on a tilted photo
    assert x0 <= 300 and x1 >= 700 and y0 <= 200 and y1 >= 600  # the whole drawing
    assert y1 < 800 and y0 > 60  # not the next figure, not the table
    assert m.snap(pg, [295, 195, 710, 610]) == [295, 195, 710, 610]  # an already-good box is untouched


def test_ut138_guide_video_provider_slots_in(tmp_path, monkeypatch):
    """FA_GUIDE_VIDEO: a provider's clip plays in place of the drawn animation; a failing one never breaks the lesson."""
    import subprocess

    from server import animate, guide_video
    page(size=(850, 1100)).save(tmp_path / "01.png")
    _, steps = m.clean_manual({"title": "", "parts": [], "steps": [
        raw_step(1, "Screw the legs on", box=(100, 100, 500, 500)), raw_step(1, "Turn it over")]}, 1)
    monkeypatch.setattr(animate, "FPS", 5)
    red = tmp_path / "red.mp4"
    subprocess.run([t.ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=2", "-pix_fmt",
                    "yuv420p", str(red)], check=True)
    calls = []

    def fake_clip(before, after, prompt, seconds, folder):
        calls.append((before.copy(), after.copy(), prompt, seconds))
        return red
    monkeypatch.setattr(guide_video, "hf_clip", fake_clip)
    monkeypatch.setattr(guide_video, "PROVIDER", "hf")
    animate.render([tmp_path / "01.png"], steps, tmp_path / "a", t.ffmpeg(), 60)
    assert [c[2] for c in calls] == ["Screw the legs on", "Turn it over"]
    assert calls[0][0].size == calls[0][1].size == (1280, 570)
    assert calls[0][0].getextrema() == ((255, 255), (255, 255), (255, 255))  # the first step starts from white
    assert calls[1][0].tobytes() == calls[0][1].tobytes()  # each step moves on from the previous step's figure
    mid = steps[0]["start"] + animate.phases(steps[0]["end"] - steps[0]["start"])["focus"][0]
    subprocess.run([t.ffmpeg(), "-loglevel", "error", "-ss", f"{mid:.2f}", "-i", str(tmp_path / "a" / "video.mp4"),
                    "-frames:v", "1", str(tmp_path / "mid.png")], check=True)
    r, g, b = Image.open(tmp_path / "mid.png").getpixel((640, 280))
    assert r > 200 and g < 60 and b < 60  # the provider's (red) clip is what plays

    def broken(*a):
        raise RuntimeError("quota")
    monkeypatch.setattr(guide_video, "hf_clip", broken)
    assert guide_video.clip(Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4)), "x", 3, tmp_path / "c") is None
    monkeypatch.setattr(guide_video, "PROVIDER", "meta")
    assert guide_video.clip(Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4)), "x", 3, tmp_path / "c") is None
    monkeypatch.setattr(guide_video, "HOSTED", True)
    monkeypatch.setattr(guide_video, "PROVIDER", "hf")
    assert guide_video.provider() == "animated"  # the hosted demo never sends a learner's manual to a third party
    monkeypatch.setattr(guide_video, "HOSTED", False)
    monkeypatch.setattr(guide_video, "PROVIDER", "sora")
    assert guide_video.provider() == "animated"  # an unknown provider name: the drawn animation
