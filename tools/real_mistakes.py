"""Real mistakes for the evaluation: YouTube "common mistakes" videos show the WRONG state on purpose, filmed by real
people. A frame every 3 s goes to eval/realmistakes/frames/<video id>/t<ms>.jpg with index.jsonl; a person then
says, on #/annotate, which tutorial step each frame shows and whether it's correct, in progress or a mistake.
The videos themselves are not kept (only the sampled frames, credited in index.jsonl).

    cd app && .venv/bin/python tools/real_mistakes.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from server.tutorials import fetch_link, ffmpeg  # noqa: E402

OUT = APP / "eval" / "realmistakes"
VIDEOS = [  # (YouTube id, the bundled tutorial its frames are judged against)
    ("cwHwB8BEBwg", "breadboard-led"),   # NT Tronix: 5 Common Breadboard Mistakes Beginners Make
    ("k-2x-uzZH8s", "breadboard-led"),   # DaniElectronics: Breadboard Tips and Tricks (& 5 COMMON MISTAKES)
    ("kVXf7B5UoI4", "jump-start-car"),   # World Mechanics: What happens when you Jump Start your car the WRONG WAY!
    ("Ql8uVhPkcLY", "jump-start-car"),   # The Pit Stop Tire Pros: How to jumpstart a car & what to know and avoid
]
EVERY_S = 3


def main() -> None:
    index = OUT / "index.jsonl"
    have = {json.loads(x)["video"] for x in index.read_text(encoding="utf-8").splitlines() if x} if index.exists() else set()
    for vid, tid in VIDEOS:
        if vid in have:
            continue
        tmp = Path(tempfile.mkdtemp(prefix="fa-real-"))
        try:
            got = fetch_link(f"https://www.youtube.com/watch?v={vid}", 900, tmp)
            frames = OUT / "frames" / vid
            frames.mkdir(parents=True, exist_ok=True)
            subprocess.run([ffmpeg(), "-loglevel", "error", "-i", str(got["path"]), "-vf", f"fps=1/{EVERY_S},scale=1280:-2",
                            "-q:v", "3", str(frames / "%04d.jpg")], check=True)
            rows = []
            for f in sorted(frames.glob("*.jpg")):
                t = (int(f.stem) - 1) * EVERY_S
                dst = frames / f"t{t * 1000:07d}.jpg"
                f.rename(dst)
                rows.append({"key": dst.relative_to(APP).as_posix(), "video": vid, "tutorial_id": tid, "t": t,
                             "title": got["title"], "creator": got["creator"], "url": f"https://www.youtube.com/watch?v={vid}"})
            with index.open("a", encoding="utf-8") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            print(f"{vid} ({got['creator']}): {len(rows)} frames for {tid}", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
