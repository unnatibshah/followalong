import os
import tempfile

import pytest

# Live state (budget.json, session logs, ...) goes to a throwaway folder, never app/data/runtime.
os.environ["FA_DATA_DIR"] = tempfile.mkdtemp(prefix="fa-test-")


def write_recording(bundled, data, name: str, tutorial_id: str, mistakes: bool) -> None:
    """A replay in the promoted format, made from the scripted checks (live_checks.jsonl) on a bundled tutorial,
    with its end frames as the "You" frames. `mistakes=False` keeps only the checks that pass."""
    import json
    import shutil
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from server import providers
    plan = providers.tutorials.plans[tutorial_id]
    rows = [r for r in providers.checks.fixture.live if r["tutorial_id"] == tutorial_id and r["kind"] != "visibility"]
    if not mistakes:
        rows = [r for r in rows if r["result"]["verdict"] == "done"]
    t0 = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
    events, results, ms = [], [], 0

    def ev(type_, step=None, **data):
        at = (t0 + timedelta(milliseconds=ms)).isoformat().replace("+00:00", "Z")
        events.append({"session_id": f"rec-{name}", "at": at, "type": type_, "tutorial_id": tutorial_id,
                       "step_id": step, "mode": "live", "data": data})
    frames = data / "recordings" / name / "frames"
    frames.mkdir(parents=True)
    ev("session_start", plan_hash=plan["plan_hash"])
    for st in plan["steps"]:
        ms += 1000
        ev("step_play", st["id"], reason="first" if st["id"] == 1 else "advance")
        ms += 10000
        ev("step_wait", st["id"])
        for r in (r for r in rows if r["step_id"] == st["id"]):
            ms += 1500
            res = r["result"]
            ev("check" if r["kind"] == "full" else "recheck", st["id"], kind=r["kind"], check_no=r["check_no"],
               trigger="still", verdict=res["verdict"])
            results.append({k: r[k] for k in ("step_id", "kind", "check_no")} | {"result": res})
            shutil.copy(Path(providers.DATA) / "tutorials" / tutorial_id / "frames" / f"step-{st['id']}.jpg",
                        frames / f"preview-{ms}.jpg")
            if res["verdict"] == "wrong":
                ev("mistake_shown", st["id"], failed_checks=[x["check_id"] for x in res["results"] if not x["pass"]])
        ms += 500
        ev("advance", st["id"])
    ev("session_end", reason="completed")
    d = bundled / name
    d.mkdir(parents=True)
    meta = {"id": name, "tutorial_id": tutorial_id, "title": "With mistakes" if mistakes else "Clean run",
            "who": "team", "volunteer_code": None, "consent_ref": None, "delete_on_request": False,
            "plan_hash": plan["plan_hash"], "prompt_version": providers.JUDGED_PROMPT["version"],
            "recorded_at": events[0]["at"]}
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results))


@pytest.fixture(scope="session", autouse=True)
def test_recordings(tmp_path_factory):
    """No replays are bundled until a real session is recorded, so tests get two made from the scripted checks on
    the Nest tutorial: `with-mistakes` (unsure, hand, wrong) and `clean`."""
    from pathlib import Path

    from server import recordings
    root = Path(tmp_path_factory.mktemp("recordings"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(recordings, "BUNDLED", root / "fixtures")
        mp.setattr(recordings, "DATA", root / "data")
        write_recording(root / "fixtures", root / "data", "with-mistakes", "install-thermostat", True)
        write_recording(root / "fixtures", root / "data", "clean", "install-thermostat", False)
        yield root


@pytest.fixture(autouse=True)
def no_key_unless_live(request, monkeypatch):
    """Offline by default: an exported MODEL_API_KEY never reaches a normal test. `-m live` tests keep it."""
    if "live" not in request.keywords:
        monkeypatch.delenv("MODEL_API_KEY", raising=False)


@pytest.fixture
def scripted(monkeypatch):
    """FA_PROVIDER=cached: live sessions without a key run on the scripted fixture checks (FR-152 refuses them with
    the default FA_PROVIDER=live)."""
    from server import providers
    monkeypatch.setattr(providers, "PROVIDER", "cached")


def make_video(seconds: float, ext: str = "mp4") -> bytes:
    """A real tiny video (ffmpeg's test pattern, 64x36, 5 fps), so uploads pass the ffmpeg checks."""
    import subprocess
    from pathlib import Path

    from server.tutorials import ffmpeg
    out = Path(tempfile.mkdtemp()) / f"t.{ext}"
    codec = ["-c:v", "libvpx-vp9"] if ext == "webm" else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    subprocess.run([ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=64x36:rate=5",
                    *codec, str(out)], check=True)
    return out.read_bytes()


@pytest.fixture(scope="session")
def mp4() -> bytes:
    return make_video(2)


@pytest.fixture(autouse=True)
def no_step_details(monkeypatch):
    """Uploads skip the voice-coach directions call (server/details.py) so pipeline tests see the calls they expect."""
    from server.config import MODELS
    monkeypatch.setitem(MODELS, "step_details", False)
