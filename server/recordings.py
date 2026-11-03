"""The `recording` provider's files (FR-142, TD §5), and a command to turn a recording into a bundled replay.

With FA_PROVIDER=recording every live session is saved under $FA_DATA_DIR/recordings/rec-<session id prefix>/ in the
format the cached provider and replay read: meta.json, timeline.jsonl (the session log), results.jsonl (one line per
check, keyed by step_id, kind, check_no) and frames/ (check-<ms>.jpg; ms from the session start). `promote` copies
them as preview-<ms>.jpg, the replay's "You" panel.

    python -m server.recordings list
    python -m server.recordings promote <id> --as with-mistakes [--title "With mistakes"] [--consent-ref FILE] [--force]
"""

import argparse
import hashlib
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .config import DATA, JUDGED_PROMPT, RUNTIME

ROOT = RUNTIME / "recordings"
BUNDLED = Path(__file__).parent / "fixtures" / "recordings"  # meta, timeline, results; frames go to DATA/recordings
log = logging.getLogger("followalong.recording")


def rec_id(sid: str) -> str:
    return f"rec-{sid[:12]}"


def _ms(session: dict) -> int:
    t0 = datetime.fromisoformat(session["started_at"].replace("Z", "+00:00"))
    return max(0, round((datetime.now(t0.tzinfo) - t0).total_seconds() * 1000))


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def start(session: dict, plan_hash: str) -> None:
    d = ROOT / rec_id(session["id"])
    (d / "frames").mkdir(parents=True, exist_ok=True)
    meta = {"id": d.name, "tutorial_id": session["tutorial_id"], "title": "Recorded session", "who": "team",
            "volunteer_code": None, "consent_ref": None, "delete_on_request": False, "plan_hash": plan_hash,
            "prompt_version": JUDGED_PROMPT["version"], "recorded_at": session["started_at"]}
    (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info(json.dumps({"event": "recording_started", "id": d.name}))


def timeline(sid: str, event: dict) -> None:
    d = ROOT / rec_id(sid)
    if d.exists():
        _append(d / "timeline.jsonl", event)


def save_check(session: dict, meta: dict, frame: bytes, result: dict) -> None:
    d = ROOT / rec_id(session["id"])
    ms = _ms(session)
    (d / "frames" / f"check-{ms}.jpg").write_bytes(frame)
    _append(d / "results.jsonl", {"step_id": meta["step_id"], "kind": meta["kind"], "check_no": meta["check_no"],
                                  "trigger": meta.get("trigger"), "frame": f"frames/check-{ms}.jpg",
                                  "frame_sha256": hashlib.sha256(frame).hexdigest(),
                                  "prompt_version": JUDGED_PROMPT["version"], "result": result})


def promote(rid: str, name: str, title: str | None, consent_ref: str | None, force: bool) -> Path:
    """Copy a recording into the bundled replays: meta/timeline/results → server/fixtures/recordings/<name>/,
    check frames → data/recordings/<name>/frames/preview-<ms>.jpg (the originals stay in $FA_DATA_DIR for evaluation)."""
    src = ROOT / rid
    if not (src / "meta.json").exists():
        raise SystemExit(f"No recording {rid} in {ROOT}.")
    if not name.replace("-", "").isalnum():
        raise SystemExit("--as must be letters, digits and dashes.")
    dst, frames = BUNDLED / name, DATA / "recordings" / name / "frames"
    if dst.exists() and not force:
        raise SystemExit(f"{dst} exists; pass --force to replace it.")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.rmtree(frames.parent, ignore_errors=True)
    dst.mkdir(parents=True)
    frames.mkdir(parents=True)
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    meta.update(id=name, title=title or meta["title"], consent_ref=consent_ref or meta.get("consent_ref"))
    (dst / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    events = [json.loads(line) for line in (src / "timeline.jsonl").read_text(encoding="utf-8").splitlines() if line]
    # browser events can arrive slightly out of order; by time, not text ("…01Z" vs "…01.500000Z")
    events.sort(key=lambda e: datetime.fromisoformat(e["at"].replace("Z", "+00:00")))
    (dst / "timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    shutil.copy(src / "results.jsonl", dst / "results.jsonl")
    for f in (src / "frames").glob("check-*.jpg"):  # recordings made before this wrote preview- copies too: same bytes
        shutil.copy(f, frames / f.name.replace("check-", "preview-", 1))
    return dst


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m server.recordings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="recordings in $FA_DATA_DIR/recordings")
    pr = sub.add_parser("promote", help="turn a recording into a bundled replay")
    pr.add_argument("id")
    pr.add_argument("--as", dest="name", required=True)
    pr.add_argument("--title")
    pr.add_argument("--consent-ref", help="file name of the signed consent form (volunteers; kept off-repo)")
    pr.add_argument("--force", action="store_true", help="replace an existing bundled replay of that name")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for d in sorted(ROOT.glob("*/meta.json")):
            m = json.loads(d.read_text(encoding="utf-8"))
            n = sum(1 for _ in (d.parent / "results.jsonl").open(encoding="utf-8")) if (d.parent / "results.jsonl").exists() else 0
            print(f"{m['id']}  {m['tutorial_id']}  {m['recorded_at']}  {n} checks")
    else:
        print(promote(a.id, a.name, a.title, a.consent_ref, a.force), file=sys.stderr)


if __name__ == "__main__":
    main()
