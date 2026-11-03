"""The static demo (GitHub Pages / any file host): the web app plus every read endpoint dumped from the real server,
so the bundled replays run with no server, no key and no camera. Live mode and uploads say they need the app.

    cd app && .venv/bin/python tools/build_static.py [--out ../site]

Layout mirrors the API (web/js/api.js `file()`): /api/x → api/x.json, timelines → .jsonl, videos → video.mp4,
end frames → frames/<n>.jpg, replay frames → frames/preview-<ms>.jpg plus frames.json (the index), results.json and
summary.json (the SessionSummary the server would compute from the recorded timeline).
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ["FA_DATA_DIR"] = tempfile.mkdtemp(prefix="fa-static-")
os.environ.pop("MODEL_API_KEY", None)  # the dump is the no-key server: live off, uploads off

from fastapi.testclient import TestClient  # noqa: E402

from server import providers, recordings  # noqa: E402
from server.main import app  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=APP.parent / "site")
    out = ap.parse_args().out.resolve()
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(APP / "web", out)
    # root-absolute asset paths → relative (the site lives under /<repo>/ on GitHub Pages)
    html = (out / "index.html").read_text(encoding="utf-8")
    html = html.replace('<html lang="en">', '<html lang="en" data-static>').replace('href="/', 'href="./').replace('src="/', 'src="./')
    (out / "index.html").write_text(html, encoding="utf-8")
    css = out / "css" / "tokens.css"
    css.write_text(css.read_text(encoding="utf-8").replace("url(/fonts/", "url(../fonts/"), encoding="utf-8")
    (out / ".nojekyll").write_text("")
    for f in (APP / "web" / "legal").glob("*.md"):
        (out / "legal").mkdir(exist_ok=True)
        shutil.copy(f, out / "legal" / f.name)

    c = TestClient(app)

    def dump(path: str, ext: str = ".json") -> object:
        r = c.get(path)
        r.raise_for_status()
        dst = out / (path.lstrip("/") + ext)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(r.content)
        return r.json() if ext == ".json" else r.text

    dump("/api/config")
    dump("/api/evaluation/latest")
    for t in dump("/api/tutorials"):
        tid = t["id"]
        dump(f"/api/tutorials/{tid}")
        plan = dump(f"/api/tutorials/{tid}/plan")
        dump(f"/api/tutorials/{tid}/details")
        dump(f"/api/creator/{tid}")
        d = out / "api" / "tutorials" / tid
        shutil.copy(APP / "data" / "tutorials" / tid / "video.mp4", d / "video.mp4")
        (d / "frames").mkdir(exist_ok=True)
        for st in plan["steps"]:
            src = APP / "data" / "tutorials" / tid / "frames" / f"step-{st['id']}.jpg"
            if src.exists():
                shutil.copy(src, d / "frames" / f"{st['id']}.jpg")
    for rec in dump("/api/recordings"):
        rid = rec["id"]
        tl = dump(f"/api/recordings/{rid}/timeline", ".jsonl")
        d = out / "api" / "recordings" / rid
        shutil.copy(recordings.BUNDLED / rid / "results.jsonl", d / "results.tmp")
        rows = [json.loads(x) for x in (d / "results.tmp").read_text(encoding="utf-8").splitlines() if x]
        (d / "results.tmp").unlink()
        (d / "results.json").write_text(json.dumps(rows), encoding="utf-8")
        events = [json.loads(x) for x in tl.splitlines() if x]
        steps = len(providers.tutorials.plans[rec["tutorial_id"]]["steps"])
        (d / "summary.json").write_text(json.dumps(providers.summarise(events, steps)), encoding="utf-8")
        (d / "frames").mkdir(exist_ok=True)
        ms = []
        for f in (recordings.DATA / "recordings" / rid / "frames").glob("preview-*.jpg"):
            shutil.copy(f, d / "frames" / f.name)
            ms.append(int(re.search(r"(\d+)", f.stem)[1]))
        (out / "api" / "recordings" / rid / "frames.json").write_text(json.dumps(sorted(ms)), encoding="utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"static demo → {out} ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
