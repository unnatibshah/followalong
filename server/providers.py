"""Where the data comes from. Step 0: fixtures for every area.

Each area is one module-level object that api.py calls (`tutorials`, `checks`, ...). To make an area real,
replace that one assignment at the bottom with the real implementation; api.py and the browser don't change.
"""

import asyncio
import copy
import hashlib
import json
import os
import secrets
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .checks import engine
from .config import APP, CACHED_RECORDING, DATA, HOSTED, JUDGED_PROMPT, LIMITS, MOTION, PROVIDER, RUNTIME, TIMEOUTS, UPLOAD, VOICE
from .gateway import CapReached, GatewayError, gateway, has_key
from . import i18n, safety
from . import voice as speech
from . import recordings as recorder
from . import manual as manuals
from . import tutorials as processing
from .store import et_now, locked_json, read_json, vhash

LOG_RETENTION_DAYS = float(os.environ.get("FA_LOG_RETENTION_DAYS", 30))

FIXTURES = Path(__file__).parent / "fixtures"
PLACEHOLDER = FIXTURES / "frames" / "placeholder.jpg"
UPLOAD_MAX_S, UPLOAD_MAX_MB = UPLOAD["max_s"], UPLOAD["max_mb"]
DEFAULT_TUTORIAL = "install-thermostat"  # scripted checks and fixture uploads borrow its plan and media


class ApiError(Exception):
    STATUS = {"bad_request": 400, "cross_origin": 403, "not_found": 404, "forbidden": 403,
              "consent_required": 409, "check_in_progress": 409, "too_long": 413, "invalid_upload": 415,
              "invalid_plan": 422, "processing_failed": 422, "cap_reached": 429, "limit_reached": 429,
              "no_api_key": 503, "too_many_pages": 413, "invalid_manual": 415, "not_licensed": 422,
              "replay_only": 409, "session_ended": 409}
    # Default wording from the UX doc; routes can pass their own.
    MESSAGES = {
        "bad_request": "The request was malformed.",
        "cross_origin": "Requests must come from this site.",
        "not_found": "That doesn't exist, or it belongs to another browser.",
        "forbidden": "You can't change this tutorial.",
        "consent_required": "Tick the box to confirm you're 18 or older.",
        "check_in_progress": "A check is already running.",
        "too_long": "This video is 4:10. The limit is 3 minutes on this demo (10 minutes when you run FollowAlong yourself).",
        "invalid_upload": "This file isn't a readable MP4 video.",
        "too_many_pages": "This manual has too many pages. Upload just the pages you need.",
        "invalid_manual": "We can't open this manual. Upload photos of the pages instead.",
        "invalid_plan": "The step plan isn't valid.",
        "processing_failed": "We couldn't turn this video into steps. Try a shorter or clearer video.",
        "cap_reached": "Live checks are paused (demo budget reached). Watch a replay instead.",
        "limit_reached": "This demo allows 1 upload per visitor a day. Try a bundled tutorial or a replay.",
        "no_api_key": "Add a Meta API key to use live mode. Replays work without one.",
        "replay_only": "The coach doesn't answer in a replay. Start a live session to ask.",
        "session_ended": "This session has ended. Start a new one.",
    }

    def __init__(self, code: str, message: str | None = None, retry_after_s: int | None = None):
        self.code, self.message, self.retry_after_s = code, message or self.MESSAGES[code], retry_after_s
        self.status = self.STATUS[code]

    def body(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.retry_after_s is not None:
            err["retry_after_s"] = self.retry_after_s
        return {"error": err}


def _remove(path: Path) -> None:
    """An upload's source: a file, or a folder of page images (a manual)."""
    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FixtureConfig:
    @property
    def live_enabled(self) -> bool:
        """FR-152: live mode needs MODEL_API_KEY. Without one, live sessions and uploads answer no_api_key, unless
        FA_PROVIDER is cached or recording: an explicit choice (tests, offline demos) of the scripted fixture checks."""
        return has_key() or PROVIDER != "live"

    def get(self) -> dict:
        return {
            "live_enabled": self.live_enabled,
            "hosted": HOSTED,
            "voice": voice.mode(),
            "limits": {"upload_max_s": UPLOAD_MAX_S,
                       "sessions_per_day": LIMITS["sessions_per_day"] if HOSTED else None,
                       "uploads_per_day": LIMITS["uploads_per_day"] if HOSTED else None,
                       "concurrent_sessions": LIMITS["concurrent"] if HOSTED else None},
            # config/motion.toml; `debug` (FA_LOG_LEVEL=debug): the browser logs its motion sample rate (PERF-06)
            "motion": {**MOTION, "debug": os.environ.get("FA_LOG_LEVEL", "info").lower() == "debug"},
            "caps_remaining_day_usd": gateway.budget.remaining_day() if gateway.budget.enforce else None,
            # Step-1 extension: "sam" (the check engine on Meta) or "fixture" with the reason, for the dev menu
            # and for anyone wondering why the verdicts look scripted.
            "live_checks": checks.source(),
            # Step-0 extension (not in the API spec): bundled tutorials with no MP4 on disk. The browser draws the stand-in scene for these instead of requesting video and frames.
            # Read on every request, so regenerating data/ never needs a server restart.
            "stand_in_media": [t for t in tutorials.bundled if not (DATA / "tutorials" / t / "video.mp4").exists()],
        }


class FixtureTutorials:
    """Bundled tutorials from fixtures. Uploads (fake processing) are saved as RUNTIME/tutorials/<id>/meta.json and
    plan edits as RUNTIME/plans/<id>/<visitor hash>.json, so both survive a restart and an edit is only the editor's."""
    PROCESS_S = 6.0  # simulated processing time for an upload

    def __init__(self):
        self.bundled = {t["id"]: t for t in _json(FIXTURES / "tutorials.json")}
        # {sha256 of a video file: bundled id}: the file it was built from and its served MP4 (written by `bundle`)
        self.bundled_hashes = _json(FIXTURES / "source_hashes.json")
        # plan_hash is recomputed from the content (replay pinning), whatever the hand-edited file says
        self.plans = {p.stem: processing.rehash(_json(p)) for p in (FIXTURES / "plans").glob("*.json")}
        # {"meta", "t0": wall-clock start}; owner_visitor is the cookie's hash (data dictionary)
        self.uploads: dict[str, dict] = {f.parent.name: _json(f) for f in (RUNTIME / "tutorials").glob("*/meta.json")}

    def _upload_meta(self, u: dict) -> dict:
        elapsed = time.time() - u["t0"]
        meta = dict(u["meta"])
        if elapsed < self.PROCESS_S * 0.7:
            meta["progress"] = f"finding_steps {min(10, int(elapsed / (self.PROCESS_S * 0.07)))}/10"
        elif elapsed < self.PROCESS_S:
            meta["progress"] = f"outlining {min(10, int((elapsed - self.PROCESS_S * 0.7) / (self.PROCESS_S * 0.03)))}/10"
        else:
            meta.update(status="ready", progress=None, step_count=10)
        return meta

    def list(self, visitor: str) -> list:
        mine = [self._upload_meta(u) for u in self.uploads.values() if u["meta"]["owner_visitor"] == vhash(visitor)]
        return list(self.bundled.values()) + mine

    def find_same(self, sha256: str, visitor: str) -> str | None:
        """A bundled tutorial, or one of this visitor's uploads (never another's), made from these exact bytes.
        ponytail: exact file match only; a re-encoded or trimmed copy of the same video needs a perceptual hash."""
        if sha256 in self.bundled_hashes:
            return self.bundled_hashes[sha256]
        return next((t for t, u in self.uploads.items() if u.get("sha256") == sha256
                     and u["meta"]["owner_visitor"] == vhash(visitor) and u["meta"]["status"] != "failed"), None)

    def get(self, tid: str, visitor: str) -> dict:
        if tid in self.bundled:
            return self.bundled[tid]
        u = self.uploads.get(tid)
        if not u or u["meta"]["owner_visitor"] != vhash(visitor):
            raise ApiError("not_found")
        return self._upload_meta(u)

    def upload(self, filename: str, visitor: str) -> dict:
        tid = secrets.token_hex(8)
        title = Path(filename or "Your video").stem[:60] or "Your video"
        meta = {"id": tid, "title": title, "source": "upload", "owner_visitor": vhash(visitor), "creator": None,
                "licence": None, "duration_s": 160.0, "step_count": None, "status": "processing",
                "progress": "finding_steps 0/10", "created_at": now_iso(), "delete_after": None}
        self.uploads[tid] = {"meta": meta, "t0": time.time()}
        (RUNTIME / "tutorials" / tid).mkdir(parents=True, exist_ok=True)
        (RUNTIME / "tutorials" / tid / "meta.json").write_text(json.dumps(self.uploads[tid]), encoding="utf-8")
        return {"id": tid, "status": "processing"}

    def delete(self, tid: str, visitor: str) -> None:
        if tid in self.bundled:
            raise ApiError("forbidden")
        u = self.uploads.get(tid)
        if not u:
            raise ApiError("not_found")
        if u["meta"]["owner_visitor"] != vhash(visitor):
            raise ApiError("forbidden")
        del self.uploads[tid]
        shutil.rmtree(RUNTIME / "tutorials" / tid, ignore_errors=True)
        shutil.rmtree(RUNTIME / "plans" / tid, ignore_errors=True)

    def forget(self, visitor: str) -> int:
        """Delete this visitor's uploads and plan edits (Delete my data). Returns how many uploads."""
        mine = vhash(visitor)
        gone = [t for t, u in self.uploads.items() if u["meta"]["owner_visitor"] == mine]
        for tid in gone:
            self._drop(tid)
        for f in (RUNTIME / "plans").glob(f"*/{mine}.json"):
            f.unlink(missing_ok=True)
        return len(gone)

    def _drop(self, tid: str) -> None:
        self.uploads.pop(tid, None)
        shutil.rmtree(RUNTIME / "tutorials" / tid, ignore_errors=True)
        shutil.rmtree(RUNTIME / "plans" / tid, ignore_errors=True)

    def _edit(self, tid: str, visitor: str) -> Path:
        return RUNTIME / "plans" / tid / f"{vhash(visitor)}.json"

    def plan(self, tid: str, visitor: str) -> dict:
        meta = self.get(tid, visitor)
        if meta["status"] != "ready":
            raise ApiError("not_found", "The step plan isn't ready yet.")
        edited = read_json(self._edit(tid, visitor), None)
        if edited:
            return edited
        if tid in self.plans:
            return self.plans[tid]
        plan = copy.deepcopy(self.plans[DEFAULT_TUTORIAL])  # a fixture upload "becomes" the default tutorial
        plan["tutorial_id"] = tid
        return processing.rehash(plan)

    def save_plan(self, tid: str, plan: dict, visitor: str) -> dict:
        meta = self.get(tid, visitor)
        if HOSTED and meta["source"] == "bundled":
            raise ApiError("forbidden")
        steps = plan.get("steps")
        if plan.get("tutorial_id") != tid or not isinstance(steps, list) or not steps:
            raise ApiError("invalid_plan")
        for s in steps:
            if not isinstance(s, dict) or not str(s.get("instruction", "")).strip():
                raise ApiError("invalid_plan", "Every step needs an instruction.")
            if not isinstance(s.get("checks"), list):
                raise ApiError("invalid_plan", "A step's checks must be a list.")
            for c in s["checks"]:  # the per-check rules of tutorials.clean_plan, as the engine reads them
                if not isinstance(c, dict) or c.get("kind") not in ("measured", "judged") or not str(c.get("text", "")).strip() \
                        or not isinstance(c.get("id"), str):
                    raise ApiError("invalid_plan", "Every check needs an id, a kind and some text.")
                typ = c.get("type")
                if typ not in (("judged",) if c["kind"] == "judged" else processing.MEASURED):
                    raise ApiError("invalid_plan", f"A {c['kind']} check can't have type {typ!r}.")
                if c["kind"] == "measured" and (not isinstance(c.get("a"), str) or (typ in processing.NEEDS_B and not isinstance(c.get("b"), str))
                                                or (typ == "count" and not (type(c.get("n")) is int and c["n"] >= 1))
                                                or (typ == "adjacent" and c.get("side") not in processing.SIDES)):
                    raise ApiError("invalid_plan", f"A {typ} check needs its objects (and n for count, side for adjacent).")
        old = self.plan(tid, visitor)  # marking a plan reviewed changes no step: keep its hash, so replays still play
        if {**plan, "plan_hash": "", "reviewed": None} == {**old, "plan_hash": "", "reviewed": None}:
            plan["plan_hash"] = old["plan_hash"]
        else:
            processing.rehash(plan)  # an edit is a new plan: recordings made on the old one no longer replay
        path = self._edit(tid, visitor)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan), encoding="utf-8")
        return plan

    def _media(self, tid: str, visitor: str) -> Path:
        self.get(tid, visitor)
        # Fixture uploads "become" the default tutorial (see plan()), so they get its media too.
        return DATA / "tutorials" / (tid if tid in self.bundled else DEFAULT_TUTORIAL)

    def video(self, tid: str, visitor: str) -> Path:
        path = self._media(tid, visitor) / "video.mp4"
        if not path.exists():
            raise ApiError("not_found", "This tutorial has no video yet.")
        return path

    def frame(self, tid: str, step: int, visitor: str) -> Path:
        path = self._media(tid, visitor) / "frames" / f"step-{step}.jpg"
        return path if path.exists() else PLACEHOLDER

    def details(self, tid: str, visitor: str) -> dict:
        """Exact spoken directions per step ({step id: {say, placements, watch_for, check_yourself}}; server/details.py),
        {} when the tutorial has none yet."""
        return safety.annotate(read_json(self._media(tid, visitor) / "details.json", {}).get("steps", {}))

    def actor(self, tid: str, visitor: str) -> str:
        """Who or what does the work in this tutorial (any noun phrase; "none"), from details.json; "hand" if unknown."""
        return read_json(self._media(tid, visitor) / "details.json", {}).get("actor") or "hand"


class Tutorials(FixtureTutorials):
    """Uploads processed for real (server/tutorials.py) when MODEL_API_KEY is set and FA_PROVIDER isn't `cached`;
    otherwise the fixture above (fake progress, the thermostat plan). Both validate the file with ffmpeg first.
    A real upload lives in RUNTIME/tutorials/<id>/ (meta.json, video.mp4, plan.json, frames/, refs/; TD §6) and is
    processed by an asyncio task: one at a time per visitor, FA_MAX_CONCURRENT_UPLOADS at once (the rest wait their
    turn, up to FA_MAX_UPLOAD_JOBS), cancelled by DELETE. Hosted: deleted after
    FA_UPLOAD_TTL_HOURS (FR-07), checked whenever uploads are listed or added."""

    def __init__(self):
        super().__init__()
        self.jobs: dict[str, asyncio.Task] = {}
        self.slots = asyncio.Semaphore(UPLOAD["concurrent"])
        for tid, u in self.uploads.items():  # a restart (or --reload) killed the task: say so instead of spinning
            if u.get("real") and u["meta"]["status"] == "processing":
                u["meta"].update(status="failed", progress=None)
                u["error"] = "interrupted by a server restart"
                self._save(tid)

    @staticmethod
    def real() -> bool:
        return has_key() and PROVIDER != "cached"

    def _dir(self, tid: str) -> Path:
        return RUNTIME / "tutorials" / tid

    def _save(self, tid: str) -> None:
        if tid in self.uploads:  # a cancelled upload's folder is gone; don't recreate it
            (self._dir(tid) / "meta.json").write_text(json.dumps(self.uploads[tid]), encoding="utf-8")

    def _upload_meta(self, u: dict) -> dict:
        return dict(u["meta"]) if u.get("real") else super()._upload_meta(u)

    def purge(self) -> None:
        if not HOSTED:
            return
        now = now_iso()
        for tid, u in list(self.uploads.items()):
            if (u["meta"].get("delete_after") or "9") < now:
                self._drop(tid)

    def _drop(self, tid: str) -> None:
        job = self.jobs.pop(tid, None)
        if job:
            job.cancel()
        self.uploads.pop(tid, None)
        shutil.rmtree(self._dir(tid), ignore_errors=True)
        shutil.rmtree(RUNTIME / "plans" / tid, ignore_errors=True)

    def list(self, visitor: str) -> list:
        self.purge()
        return super().list(visitor)

    async def receive(self, file, max_bytes: int) -> tuple[str, Path, dict, str]:
        """Save the upload under a new id (size-capped and hashed while copying) and check it with ffmpeg before any
        model call. Returns (id, path, ffmpeg info, sha256). Raises ApiError; nothing is kept on a refusal."""
        tid = secrets.token_hex(8)
        d = self._dir(tid)
        d.mkdir(parents=True)
        src = d / "source"
        digest = hashlib.sha256()
        try:
            size = 0
            with open(src, "wb") as f:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ApiError("too_long", f"This file is over {UPLOAD_MAX_MB} MB, the limit here.")
                    f.write(chunk)
                    digest.update(chunk)
            info = await processing.probe(src)
            processing.check_upload(info, UPLOAD_MAX_S, UPLOAD["convert"])
        except processing.Rejected as e:
            shutil.rmtree(d, ignore_errors=True)
            raise ApiError(e.code, e.message)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        return tid, src, info, digest.hexdigest()

    async def receive_manual(self, files: list, max_bytes: int) -> tuple[str, Path, dict, str]:
        """A manual (CR-006, FR-160): one PDF, or page images in order. Saved under a new id (size-capped and hashed
        while copying: all files count toward the upload cap) and checked before any model call. Returns (id, the PDF
        or the folder of pages, {"kind", "pages", "duration": 0}, sha256). Raises ApiError; nothing kept on a refusal."""
        if len(files) > manuals.MAX_IMAGES:  # before reading any of them
            raise ApiError("too_many_pages", f"That's {len(files)} photos. The limit is {manuals.MAX_IMAGES}: "
                           "upload just the pages you need.")
        tid = secrets.token_hex(8)
        d = self._dir(tid)
        d.mkdir(parents=True)
        digest, size = hashlib.sha256(), 0
        pdf = len(files) == 1 and (Path(files[0].filename or "").suffix.lower() == ".pdf"
                                   or getattr(files[0], "content_type", None) == "application/pdf")
        src = d / "source.pdf" if pdf else d / "source"
        try:
            if not pdf:
                src.mkdir()
            for i, file in enumerate(files, 1):
                ext = Path(file.filename or "").suffix.lower()
                if not pdf and ext not in manuals.IMAGE_TYPES:
                    raise ApiError("invalid_upload", "Upload the manual as one PDF, or as photos of its pages "
                                   "(JPEG, PNG or WebP).")
                if not pdf:  # page boundaries count: photos [A, B] are not the one file A+B
                    digest.update(f"\0page {i}\0".encode())
                with open(src if pdf else src / f"{i:02d}{ext}", "wb") as f:
                    while chunk := await file.read(1 << 20):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ApiError("too_long", f"This upload is over {UPLOAD_MAX_MB} MB, the limit here.")
                        f.write(chunk)
                        digest.update(chunk)
            pages = await (manuals.check_pdf(src) if pdf else asyncio.to_thread(manuals.check_images, sorted(src.iterdir())))
        except manuals.Rejected as e:
            shutil.rmtree(d, ignore_errors=True)
            raise ApiError(e.code, e.message)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        return tid, src, {"kind": "pdf" if pdf else "images", "pages": pages, "duration": 0.0}, digest.hexdigest()

    def discard(self, tid: str) -> None:
        shutil.rmtree(self._dir(tid), ignore_errors=True)

    def busy(self, visitor: str) -> bool:
        return any(not j.done() and self.uploads[t]["meta"]["owner_visitor"] == vhash(visitor)
                   for t, j in self.jobs.items() if t in self.uploads)

    def full(self) -> bool:
        """FA_MAX_UPLOAD_JOBS running or queued, from anyone (the cookie is free to drop, so this bounds a script)."""
        return sum(not j.done() for j in self.jobs.values()) >= UPLOAD["jobs"]

    def start(self, tid: str, src: Path, info: dict, filename: str, visitor: str, sha256: str,
              creator: str | None = None, title: str | None = None) -> dict:
        """`info` from receive() (ffmpeg) or receive_manual() (`kind` set: a manual, CR-006)."""
        self.purge()
        manual = info.get("kind") in ("pdf", "images")
        title = (title or Path(filename or "").stem or ("Your manual" if manual else "Your video"))[:60]
        created = datetime.now(timezone.utc)
        meta = {"id": tid, "title": title, "source": "upload", "owner_visitor": vhash(visitor), "creator": creator,
                "licence": "Creative Commons video, credited" if creator else None, "duration_s": round(info["duration"], 1), "step_count": None, "status": "processing",
                "progress": "finding_steps 0/10", "created_at": created.isoformat().replace("+00:00", "Z"),
                "delete_after": (created + timedelta(hours=UPLOAD["ttl_h"])).isoformat().replace("+00:00", "Z")
                if HOSTED else None}
        if manual:  # FR-165: labelled, with its page count; the length is known once the video is made
            meta.update(source="manual", manual={"kind": info["kind"], "pages": info["pages"]}, duration_s=None)
        real = self.real()
        self.uploads[tid] = {"meta": meta, "t0": time.time(), "real": real, "sha256": sha256}
        if real:
            meta["progress"] = "finding_steps 0/0"
            self.jobs[tid] = asyncio.create_task(self._process(tid, src, info))
        else:
            _remove(src)
        self._save(tid)
        return {"id": tid, "status": "processing"}

    async def _process(self, tid: str, src: Path, info: dict) -> None:
        u = self.uploads[tid]

        def progress(text: str) -> None:
            u["meta"]["progress"] = text
            self._save(tid)
        try:
            if self.slots.locked():
                progress("queued 0/0")  # S2 says it's waiting its turn
            async with self.slots:
                progress("finding_steps 0/0")
                if u["meta"]["source"] == "manual":
                    plan, stats = await manuals.process(src, info["kind"], self._dir(tid), tid, progress)
                    u["meta"]["duration_s"] = stats["duration_s"]
                    if plan["manual"]["title"]:  # what is being built reads better than a file name
                        u["meta"]["title"] = plan["manual"]["title"]
                else:
                    plan, stats = await processing.process(src, self._dir(tid), tid, info, progress)
            (self._dir(tid) / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            u["meta"].update(status="ready", progress=None, step_count=len(plan["steps"]))
            u["stats"] = stats
        except asyncio.CancelledError:
            raise
        except Exception as e:  # nothing but the failed status is kept (TD §8)
            u["meta"].update(status="failed", progress=None)
            u["error"] = str(e) if isinstance(e, processing.Failed) else type(e).__name__
            processing.log.warning(json.dumps({"event": "processing_failed", "tutorial_id": tid, "error": u["error"]}))
            (self._dir(tid) / "video.mp4").unlink(missing_ok=True)
            for name in ("frames", "refs", "pages", "clips"):
                shutil.rmtree(self._dir(tid) / name, ignore_errors=True)
        finally:
            _remove(src)
            self.jobs.pop(tid, None)
            self._save(tid)

    def delete(self, tid: str, visitor: str) -> None:
        super().delete(tid, visitor)  # ownership rules, then the files
        self._drop(tid)

    def plan(self, tid: str, visitor: str) -> dict:
        u = self.uploads.get(tid)
        if not (u and u.get("real")):
            return super().plan(tid, visitor)
        if self.get(tid, visitor)["status"] != "ready":
            raise ApiError("not_found", "The step plan isn't ready yet.")
        return read_json(self._edit(tid, visitor), None) or _json(self._dir(tid) / "plan.json")

    def _media(self, tid: str, visitor: str) -> Path:
        u = self.uploads.get(tid)
        if u and u.get("real"):
            self.get(tid, visitor)
            return self._dir(tid)
        return super()._media(tid, visitor)

    def details(self, tid: str, visitor: str) -> dict:
        """A manual's own warnings (FR-168) fill any step whose directions have no safety line, so the tiers apply
        before (or without) the directions pass."""
        steps = super().details(tid, visitor)
        u = self.uploads.get(tid)
        if not (u and u.get("real") and u["meta"].get("source") == "manual" and u["meta"]["status"] == "ready"):
            return steps
        for s in _json(self._dir(tid) / "plan.json")["steps"]:
            if s.get("warning"):
                d = steps.setdefault(str(s["id"]), {"say": s["instruction"], "placements": [], "watch_for": [],
                                                    "check_yourself": "", "safety": ""})
                if not d.get("safety"):
                    d["safety"] = s["warning"]
        return safety.annotate(steps)  # the same per-step and lesson rules as any guide: a warning never lowers a tier


class FixtureRecordings:
    """Bundled replays: server/fixtures/recordings/<id>/ (meta, timeline, results) + data/recordings/<id>/frames/.
    Scanned on every call, so one promoted with `python -m server.recordings promote` shows up without a restart."""

    @property
    def dirs(self) -> dict:
        root = recorder.BUNDLED  # none until a real session is promoted
        return {d.name: d for d in root.iterdir() if (d / "meta.json").exists()} if root.exists() else {}

    def list(self) -> list:
        return [_json(d / "meta.json") for d in self.dirs.values()]

    def _dir(self, rid: str) -> Path:
        if rid not in self.dirs:
            raise ApiError("not_found")
        return self.dirs[rid]

    def meta(self, rid: str) -> dict:
        return _json(self._dir(rid) / "meta.json")

    def pin(self, rid: str, plan: dict, prompt: bool = True) -> None:
        """UT-77: a recording answers only for the step plan (and, for live answers from it, the judged-check prompt) it
        was recorded with. A replay plays back recorded answers, so a newer prompt can't change it: plan only."""
        m = self.meta(rid)
        if m.get("plan_hash") != plan["plan_hash"]:
            raise ApiError("not_found", f"Recording {rid} was made with a different step plan ({m.get('plan_hash', '?')[:15]}…, "
                                        f"the tutorial now has {plan['plan_hash'][:15]}…). Re-record it, or undo the plan edits.")
        if prompt and m.get("prompt_version") != JUDGED_PROMPT["version"]:
            raise ApiError("not_found", f"Recording {rid} was made with prompt {m.get('prompt_version')}; this server runs "
                                        f"{JUDGED_PROMPT['version']}. Re-record it.")

    def timeline(self, rid: str) -> str:
        return (self._dir(rid) / "timeline.jsonl").read_text(encoding="utf-8")

    def results(self, rid: str) -> list:
        return _jsonl(self._dir(rid) / "results.jsonl")

    def frame(self, rid: str, t: str) -> Path:
        """The preview frame at or before `t` (ms from the recording's start), so any replay clock time works.
        ponytail: one frame per timeline event, not the 2 fps of real recordings; same lookup either way."""
        self._dir(rid)
        if not (t.isascii() and t.isdigit()):  # "²".isdigit() is True, int("²") is not
            raise ApiError("not_found", "A frame time is a whole number of milliseconds.")
        frames = sorted(((int(f.stem.split("-")[1]), f) for f in (recorder.DATA / "recordings" / rid / "frames").glob("preview-*.jpg")))
        before = [f for ms, f in frames if ms <= int(t)] or [f for _, f in frames[:1]]
        return before[-1] if before else PLACEHOLDER


class FixtureChecks:
    """Replay: answered from the recording, keyed by (step_id, kind, check_no); a miss is an error.
    Live: answered from a scripted sequence per tutorial (live_checks.jsonl) so the whole loop can be seen.
    Nest thermostat: unsure at step 1 (the wiring photo), waiting (hand) at step 3, wrong at step 4 (red wire not in
    Rc yet), done otherwise. Other tutorials and uploads reuse that script."""

    FORCED = ("sam_failed", "judge_failed", "hand_blocked", "object_missing")

    def __init__(self):
        self.live = _jsonl(FIXTURES / "live_checks.jsonl")

    async def run(self, session: dict, meta: dict, force: str | None, frame: bytes) -> dict:
        if session["mode"] != "replay":  # replay timing comes from the recording, so skipping ahead stays quick
            await asyncio.sleep(0.9 if meta["kind"] == "full" else 0.4)  # so the "Checking" state is visible
        step, kind, n = meta["step_id"], meta["kind"], meta["check_no"]
        if session["mode"] == "replay":
            rows = recordings.results(session["recording_id"])
            hit = next((r for r in rows if (r["step_id"], r["kind"], r["check_no"]) == (step, kind, n)), None)
            if not hit:
                raise ApiError("not_found", f"No recorded result for step {step}, {kind} check {n}.")
            return copy.deepcopy(hit["result"])
        if CACHED_RECORDING and kind != "visibility":  # SIT: live-mode rules with answers from a recording (S6's
            # visibility check isn't in recordings, so it keeps the script)
            rows = [r for r in recordings.results(CACHED_RECORDING) if r["step_id"] == step and r["kind"] == kind]
            if not rows:
                raise ApiError("not_found", f"{CACHED_RECORDING} has no {kind} check at step {step}.")
            res = copy.deepcopy(max([r for r in rows if r["check_no"] <= n] or rows, key=lambda r: r["check_no"])["result"])
            try:  # the recorded cost counts as spend, so the caps can be tested without a key
                gateway.budget.reserve(session["id"], res["cost_usd"])
            except CapReached as e:
                raise capped(e)
            return self._forced(res, force) if force in self.FORCED else res
        tid = session["tutorial_id"]
        live = [r for r in self.live if r["tutorial_id"] == tid] or \
               [r for r in self.live if r["tutorial_id"] == DEFAULT_TUTORIAL]
        same = [r for r in live if r["step_id"] == step and r["kind"] == kind and r["check_no"] <= n]
        if not same:  # unscripted (step, kind): reuse any recheck of the step, which is always "done"
            same = [r for r in live if r["step_id"] == step and r["kind"] == "recheck"] or \
                   [r for r in live if r["kind"] == "recheck"]
        res = copy.deepcopy(max(same, key=lambda r: r["check_no"])["result"])
        return self._forced(res, force) if force in self.FORCED else res

    @staticmethod
    def _forced(res: dict, force: str) -> dict:
        if force == "sam_failed":
            res.update(verdict="waiting", fallback="sam_failed", results=[], objects=[], hint=None, models=["sam-3.1"])
        elif force == "judge_failed":
            res.update(verdict="unsure", fallback="judge_failed", hint=None,
                       results=[r for r in res["results"] if r["kind"] == "measured"])
        elif force == "hand_blocked":
            res.update(verdict="waiting", fallback=None, results=[], objects=[], hint=None, hand_blocked=True)
        elif force == "object_missing" and res["objects"]:
            res["objects"][-1].update(found=False, box=None, outline_svg=None)
            res.update(verdict="unsure", fallback=None, hint=None,
                       results=[r for r in res["results"] if r["kind"] == "judged"])
        return res


class LiveChecks:
    """Live sessions: the check engine (SAM 3.1, measured checks, Muse Spark judged checks) when FA_PROVIDER=live and MODEL_API_KEY is set.
    Everything else goes to the fixture: replay sessions always, live sessions without a key or with
    FA_PROVIDER=cached, and dev `?force=` fallbacks."""

    def __init__(self, fixture: FixtureChecks):
        self.fixture = fixture

    def source(self) -> dict:
        if PROVIDER == "cached":
            return {"source": "fixture", "reason": f"FA_CACHED_RECORDING={CACHED_RECORDING}" if CACHED_RECORDING
                    else "FA_PROVIDER=cached"}
        if not has_key():
            return {"source": "fixture", "reason": "MODEL_API_KEY is not set"}
        return {"source": "sam", "reason": None}

    async def run(self, session: dict, meta: dict, force: str | None, frame: bytes) -> dict:
        res = await self._run(session, meta, force, frame)
        if PROVIDER == "recording" and session["mode"] == "live" and not force:
            recorder.save_check(session, meta, frame, res)
        return res

    async def _run(self, session: dict, meta: dict, force: str | None, frame: bytes) -> dict:
        if session["mode"] == "live" and not config.live_enabled:  # FR-152: never scripted verdicts as if real
            raise ApiError("no_api_key")
        if session["mode"] == "replay" or force or self.source()["source"] != "sam":
            return await self.fixture.run(session, meta, force, frame)
        plan = tutorials.plan(session["tutorial_id"], session["visitor"])
        step = next((s for s in plan["steps"] if s["id"] == meta["step_id"]), None)
        if step is None:
            raise ApiError("bad_request", f"Step {meta['step_id']} isn't in this tutorial.")
        path = tutorials.frame(session["tutorial_id"], step["id"], session["visitor"])
        try:
            step = {**step, "actor": tutorials.actor(session["tutorial_id"], session["visitor"])}
            return await engine.run(step, frame, meta["kind"], session["id"], path.read_bytes(), end_box(path, step))
        except CapReached as e:
            raise capped(e)


def end_box(frame: Path, step: dict) -> list[int] | None:
    """The union box of the step's objects on its end frame, from the build-time SAM outlines (refs/step-<n>.json
    next to frames/), so Muse Spark sees the tutorial cropped like the learner. None when there are none."""
    names = {o["name"] for o in step.get("objects") or []}
    refs = read_json(frame.parent.parent / "refs" / f"step-{step['id']}.json", {}).get("objects", [])
    boxes = [o["box"] for o in refs if o.get("found") and o.get("box") and o["name"] in names]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def languages(session: dict) -> list[str]:
    """Speech recognition bias: the learner's language, then English (the commands are English words)."""
    name = i18n.name(session.get("lang"))
    return [name] if name == "English" else [name, "English"]


def capped(e: CapReached) -> "ApiError":
    return ApiError("cap_reached", None, e.retry_after_s)


class FixtureVoice:
    async def watch(self, session: dict, meta: dict, frame: bytes) -> dict:
        return {"status": "working", "observation": "", "say": "", "latency_ms": 0}

    async def answer(self, session: dict, meta: dict, frame: bytes) -> dict:
        return {"answer": "Loosen the screw a turn, slide the bare copper under it, then tighten until a tug doesn't move the wire.",
                "latency_ms": 1900}

    async def transcribe(self, session: dict, audio: bytes, seconds: float) -> dict:
        return {"transcript": "next", "command": "next"}


class LiveVoice:
    """Muse Voice Transcribe (push-to-talk clips; the realtime proxy is in voice.py) and Muse Spark answers, for live
    sessions with a key and FA_PROVIDER=live or recording. Replay sessions, no key or FA_PROVIDER=cached: the fixture.
    `refused()` is called when Meta refuses a realtime session: /api/config offers push-to-talk instead, and realtime
    is tried again after `realtime_retry_s` (config/timeouts.toml). In memory, so a restart also tries again."""

    def __init__(self, fixture: FixtureVoice):
        self.fixture, self.refused_at = fixture, None

    def refused(self) -> None:
        self.refused_at = time.monotonic()

    def cooling_down(self) -> bool:
        return self.refused_at is not None and time.monotonic() - self.refused_at < TIMEOUTS["realtime_retry_s"]

    def real(self, session: dict | None = None) -> bool:
        return has_key() and PROVIDER != "cached"  # live and replay sessions: the coach answers in both

    def mode(self) -> str:
        if VOICE["mode"] == "realtime" and (not self.real() or self.cooling_down()):
            return "push_to_talk"
        return VOICE["mode"]

    async def transcribe(self, session: dict, audio: bytes, seconds: float) -> dict:
        if speech.audio_left(session) < seconds:
            raise ApiError("cap_reached", "Voice is paused for this session (audio limit reached). Buttons and shortcuts still work.")
        speech.count_audio(session, seconds)
        if not self.real(session):
            return await self.fixture.transcribe(session, audio, seconds)
        try:
            data, _ = await gateway.transcribe(audio, seconds, session["id"], languages=languages(session))
        except CapReached as e:
            raise capped(e)
        except GatewayError:  # a model failure isn't an error (API spec §1): nothing heard, the browser says so
            return {"transcript": ""}
        text = (data.get("transcript") or "").strip()
        name = speech.command(text)
        return {"transcript": text, "command": name} if name else {"transcript": text}

    async def answer(self, session: dict, meta: dict, frame: bytes) -> dict:
        if not self.real(session):
            return await self.fixture.answer(session, meta, frame)
        plan = tutorials.plan(session["tutorial_id"], session["visitor"])
        step = next((s for s in plan["steps"] if s["id"] == meta.get("step_id")), None)
        if step is None:
            raise ApiError("bad_request", f"Step {meta.get('step_id')} isn't in this tutorial.")
        try:
            end = tutorials.frame(session["tutorial_id"], step["id"], session["visitor"])
            plan = {**plan, "title": tutorials.get(session["tutorial_id"], session["visitor"])["title"],
                    "actor": tutorials.actor(session["tutorial_id"], session["visitor"])}
            meta = {**meta, "language": i18n.name(session.get("lang"))}
            res = await speech.coach(plan, tutorials.details(session["tutorial_id"], session["visitor"]), step, meta,
                                     frame, end.read_bytes() if end != PLACEHOLDER else None, session["id"])
        except CapReached as e:
            raise capped(e)
        except GatewayError as e:  # the code (never the text) goes in the log, so a fallback can be explained
            speech.log.warning(json.dumps({"event": "question_fallback", "error": e.code, "status": e.status}))
            return {"answer": "Muse Spark didn't answer just now. Try asking again.", "latency_ms": 0}
        except OSError:  # Pillow can't read the frame
            raise ApiError("bad_request", "The frame must be a JPEG.")
        return {"answer": res["answer"], "latency_ms": res["latency_ms"]}


    async def watch(self, session: dict, meta: dict, frame: bytes) -> dict:
        """The live watcher: one look by Muse Spark at the live frame (live sessions with a key)."""
        if not self.real(session) or session["mode"] != "live":
            return await self.fixture.watch(session, meta, frame)
        plan = tutorials.plan(session["tutorial_id"], session["visitor"])
        step = next((s for s in plan["steps"] if s["id"] == meta.get("step_id")), None)
        if step is None:
            raise ApiError("bad_request", f"Step {meta.get('step_id')} isn't in this tutorial.")
        tid, vis = session["tutorial_id"], session["visitor"]
        plan = {**plan, "title": tutorials.get(tid, vis)["title"], "actor": tutorials.actor(tid, vis)}
        end = tutorials.frame(tid, step["id"], vis)
        try:
            meta = {**meta, "language": i18n.name(session.get("lang"))}
            res = await speech.watch(plan, tutorials.details(tid, vis), step, meta, frame,
                                     end.read_bytes() if end != PLACEHOLDER else None, session["id"])
        except CapReached as e:
            raise capped(e)
        except GatewayError as e:  # a missed look isn't an error: the next one comes in a few seconds
            if e.code != "busy":
                speech.log.warning(json.dumps({"event": "watch_fallback", "error": e.code, "status": e.status}))
            return {"status": "busy" if e.code == "busy" else "working", "observation": "", "say": "", "latency_ms": 0,
                    "next_look_ms": gateway.limiter.next_look_ms()}
        except OSError:
            raise ApiError("bad_request", "The frame must be a JPEG.")
        return {**{k: res.get(k) for k in ("status", "observation", "on_action", "substitute", "say", "latency_ms")},
                "next_look_ms": gateway.limiter.next_look_ms()}


class Insights:
    """Creator view (FR-130, FR-131) from the live session logs, aggregates only; the sample fixture until a tutorial
    has CREATOR_MIN_SESSIONS of them (`sample: true`). Evaluation: the newest eval/reports/*.json (FR-143), else the
    placeholder."""
    CREATOR_MIN_SESSIONS = 3
    STUCK_S = 120  # FR-130: more than 2 minutes at a step

    def __init__(self, sessions_dir: Path, reports_dir: Path):
        self.sessions_dir, self.reports_dir = sessions_dir, reports_dir

    def creator(self, tid: str, plan: dict) -> dict:
        logs = []
        for f in self.sessions_dir.glob("*.jsonl"):
            try:
                events = _jsonl(f)
                for e in events:  # what creator_rows reads; a log that breaks it is skipped, not a 500 for everyone
                    _ts(e["at"])
                    if not (isinstance(e["type"], str) and isinstance(e.get("data", {}), dict)):
                        raise ValueError("not an event")
            except (ValueError, KeyError, TypeError):
                continue
            if events and events[0]["type"] == "session_start" and events[0]["tutorial_id"] == tid \
                    and events[0]["mode"] == "live":  # replays re-run a recording; they aren't learners
                logs.append(events)
        if len(logs) < self.CREATOR_MIN_SESSIONS:
            path = FIXTURES / f"creator-{tid}.json"
            rows = _json(path) if path.exists() else sample_rows(plan)
            return {"sample": True, "sessions": max([r["sessions"] for r in rows], default=0), "steps": rows}
        return {"sample": False, "sessions": len(logs), "steps": creator_rows(logs, plan, self.STUCK_S)}

    def funnel(self, days: float = 7) -> dict:
        """Product numbers from the live session logs of the last `days`: how far people get, where they disagree with
        the coach, how often safety stops them. Aggregates only."""
        since = time.time() - days * 86400
        n = {"sessions": 0, "completed": 0, "steps_done": 0, "mistakes_shown": 0, "asked": 0, "overrides": 0,
             "safety_confirmed": 0, "resumed": 0}
        minutes = []
        for f in self.sessions_dir.glob("*.jsonl"):
            if f.stat().st_mtime < since:
                continue
            try:
                events = _jsonl(f)
            except ValueError:
                continue
            if not events or events[0].get("mode") != "live":
                continue
            n["sessions"] += 1
            types = [e.get("type") for e in events]
            end = next((e for e in events if e.get("type") == "session_end"), None)
            n["completed"] += bool(end and end.get("data", {}).get("reason") == "completed")
            n["steps_done"] += types.count("advance")
            n["mistakes_shown"] += types.count("mistake_shown")
            n["asked"] += types.count("ask_shown")
            n["overrides"] += types.count("override")
            n["safety_confirmed"] += types.count("safety_confirmed")
            n["resumed"] += types.count("resumed")
            if end:
                try:
                    minutes.append((_ts(end["at"]) - _ts(events[0]["at"])) / 60)
                except (KeyError, ValueError):
                    pass
        minutes.sort()
        n["completion_rate"] = round(n["completed"] / n["sessions"], 2) if n["sessions"] else None
        n["median_minutes"] = round(minutes[len(minutes) // 2], 1) if minutes else None
        return n

    def evaluation(self) -> dict:
        reports = [_json(f) for f in self.reports_dir.glob("*.json")]
        return max(reports, key=lambda r: r["run_at"]) if reports else _json(FIXTURES / "evaluation.json")


def sample_rows(plan: dict, sessions: int = 12) -> list[dict]:
    """An illustrative creator view until real sessions exist (the page labels it sample data): steps with more checks
    are where people tend to get stuck; each step's top mistakes are its own common mistakes. Deterministic."""
    rows = []
    for st in plan["steps"]:
        weight = len(st.get("checks") or []) + len(st.get("common_mistakes") or [])
        spread = sum(map(ord, st.get("instruction", ""))) % 17 / 100  # stable per step, so steps differ
        rate = round(min(0.42, 0.03 + 0.02 * weight + spread), 2)
        rows.append({"step_id": st["id"], "sessions": sessions, "stuck_rate": rate,
                     "top_mistakes": [m for m in (st.get("common_mistakes") or []) if isinstance(m, str)][:2]})
    return rows


def creator_rows(logs: list[list[dict]], plan: dict, stuck_s: float = 120) -> list[dict]:
    """CreatorRow per plan step: sessions that reached it, the share stuck there (a `wrong` verdict, or more than
    `stuck_s` from its first `step_wait` to its `advance`, or to the session's last event if it never advanced) and
    the three most-failed checks of its `mistake_shown` events, as the plan's check texts."""
    texts = {c["id"]: c["text"] for s in plan["steps"] for c in s["checks"]}
    rows = []
    for st in plan["steps"]:
        reached = stuck = 0
        fails: dict[str, int] = {}
        for events in logs:
            mine = [e for e in events if e.get("step_id") == st["id"]]
            if not mine:
                continue
            reached += 1
            wrong = any(e["type"] in ("check", "recheck") and e["data"].get("verdict") == "wrong" for e in mine)
            wait = next((_ts(e["at"]) for e in mine if e["type"] == "step_wait"), None)
            until = next((_ts(e["at"]) for e in mine if e["type"] == "advance"), _ts(events[-1]["at"]))
            stuck += wrong or (wait is not None and until - wait > stuck_s)
            for e in mine:
                if e["type"] == "mistake_shown":
                    ids = e["data"].get("failed_checks")
                    for cid in {c for c in ids if isinstance(c, str) and c in texts} if isinstance(ids, list) else ():
                        fails[cid] = fails.get(cid, 0) + 1
        top = sorted(fails, key=lambda k: -fails[k])[:3]
        rows.append({"step_id": st["id"], "sessions": reached, "stuck_rate": round(stuck / reached, 2) if reached else 0.0,
                     "top_mistakes": [texts[cid] for cid in top]})
    return rows


class Visitors:
    """data/visitors.json (FR-124, hosted only): live sessions and uploads per cookie hash per ET day, and the live
    sessions running now. A live session's `last_seen` moves on every request to it (checks, events, the 60 s
    heartbeat); one idle for FA_SESSION_IDLE_MINUTES is expired when the next live session asks for a slot."""

    def __init__(self, path: Path, limits: dict, enforce: bool, clock=et_now):
        self.path, self.limits, self.enforce, self.clock = path, limits, enforce, clock

    def _open(self):
        return locked_json(self.path, {})

    def _fresh(self, v: dict) -> dict:
        day = self.clock().date().isoformat()
        if v.get("day") != day:  # daily counts reset; running sessions stay
            v.update(day=day, visitors={}, ips={})
        v.setdefault("active_sessions", [])
        return v

    def admit_live(self, visitor: str, sid: str, ip: str | None = None) -> list:
        """Count a new live session or raise limit_reached. Returns the ids of the sessions that just expired.
        Per cookie, and per client IP (hashed), so a script that drops its cookie can't start one after another."""
        if not self.enforce:
            return []
        now = self.clock().timestamp()
        with self._open() as v:
            self._fresh(v)
            expired = [a["session_id"] for a in v["active_sessions"] if now - a["last_seen"] > self.limits["idle_s"]
                       or now - a.get("started", now) > self.limits["max_age_s"]]
            v["active_sessions"] = [a for a in v["active_sessions"] if a["session_id"] not in expired]
            me = v["visitors"].setdefault(vhash(visitor), {"live_sessions": 0, "uploads": 0})
            ips, here = v.setdefault("ips", {}), vhash(f"ip:{ip}")
            if me["live_sessions"] >= self.limits["sessions_per_day"] or ips.get(here, 0) >= self.limits["ip_sessions_per_day"]:
                err = ApiError("limit_reached", f"You've used today's {self.limits['sessions_per_day']} live sessions. "
                                                "Watch a replay or try again later.")
            elif len(v["active_sessions"]) >= self.limits["concurrent"]:
                err = ApiError("limit_reached", f"{self.limits['concurrent']} live sessions are already running. "
                                                "Watch a replay or try again later.")
            else:
                err = None
                me["live_sessions"] += 1
                ips[here] = ips.get(here, 0) + 1
                v["active_sessions"].append({"session_id": sid, "last_seen": now, "started": now})
        for e in expired:
            if sessions._load(e):
                sessions.end(e, "expired")
        if err:
            raise err
        return expired

    def admit_upload(self, visitor: str) -> None:
        if not self.enforce:
            return
        with self._open() as v:
            me = self._fresh(v)["visitors"].setdefault(vhash(visitor), {"live_sessions": 0, "uploads": 0})
            if me["uploads"] >= self.limits["uploads_per_day"]:
                raise ApiError("limit_reached")  # raising inside the block skips the write-back
            me["uploads"] += 1

    def forget(self, visitor: str) -> None:
        with self._open() as v:
            self._fresh(v)["visitors"].pop(vhash(visitor), None)

    def touch(self, sid: str, end: bool = False) -> None:
        if not self.enforce:
            return
        with self._open() as v:
            act = self._fresh(v)["active_sessions"]
            if end:
                act[:] = [a for a in act if a["session_id"] != sid]
            for a in act:
                if a["session_id"] == sid:
                    a["last_seen"] = self.clock().timestamp()


EVENT_DATA_MAX = 2048  # bytes of JSON; the browser's largest (mistake_shown) is well under 1 KB
LOG_MAX_BYTES = 1024 * 1024  # a session log past this takes no more events (a real lesson writes ~50 KB)


class FileSessions:
    """Session logs as JSON Lines, one file per session: RUNTIME/sessions/<id>.jsonl (no frames, audio or text).
    The session itself is rebuilt from its `session_start` line, so sessions and summaries survive a restart.
    `session_start` data carries `visitor_hash` and `recording_id` too (to bind the session after a restart)."""

    def __init__(self, root: Path):
        self.root = root
        self.sessions: dict[str, dict] = {}  # cache; `busy` (one check at a time) is in memory only

    def _path(self, sid: str) -> Path:
        return self.root / f"{sid}.jsonl"

    def start(self, visitor: str, tutorial_id: str, mode: str, recording_id: str | None, plan_hash: str,
              lang: str = "en", ip: str | None = None) -> str:
        sid, at = secrets.token_hex(16), now_iso()
        self.purge()
        if mode == "live":
            visitors.admit_live(visitor, sid, ip)
        if PROVIDER == "recording" and mode == "live":
            recorder.start({"id": sid, "tutorial_id": tutorial_id, "started_at": at}, plan_hash)
        self.root.mkdir(parents=True, exist_ok=True)
        self._write(sid, {"session_id": sid, "at": at, "type": "session_start", "tutorial_id": tutorial_id,
                          "step_id": None, "mode": mode, "data": {"plan_hash": plan_hash, "recording_id": recording_id,
                                                                   "visitor_hash": vhash(visitor), "lang": lang}})
        return sid

    def purge(self, now: float | None = None) -> None:
        """Session logs are kept FA_LOG_RETENTION_DAYS (privacy notice), then deleted.
        ponytail: a directory scan per new session; a dated index if logs reach the tens of thousands."""
        cutoff = (now or time.time()) - LOG_RETENTION_DAYS * 86400
        for f in self.root.glob("*.jsonl"):
            if f.stat().st_mtime < cutoff:
                self.sessions.pop(f.stem, None)
                f.unlink(missing_ok=True)

    def forget(self, visitor: str) -> int:
        """Delete every session log of this visitor (Delete my data). Returns how many."""
        mine = vhash(visitor)
        n = 0
        for f in self.root.glob("*.jsonl"):
            try:
                first = json.loads(f.open(encoding="utf-8").readline())
            except (ValueError, OSError):
                continue
            if first.get("data", {}).get("visitor_hash") == mine:
                self.sessions.pop(f.stem, None)
                f.unlink(missing_ok=True)
                n += 1
        return n

    def _load(self, sid: str) -> dict | None:
        if sid not in self.sessions:
            if not sid.isalnum() or not self._path(sid).exists():
                return None
            first = json.loads(self._path(sid).open(encoding="utf-8").readline())
            self.sessions[sid] = {"id": sid, "visitor_hash": first["data"]["visitor_hash"],
                                  "tutorial_id": first["tutorial_id"], "mode": first["mode"],
                                  "recording_id": first["data"].get("recording_id"), "started_at": first["at"],
                                  "lang": first["data"].get("lang") or "en", "busy": False,
                                  "ended": any(e["type"] == "session_end" for e in self.events(sid))}
        return self.sessions[sid]

    def get(self, sid: str, visitor: str, ended_ok: bool = False) -> dict:
        s = self._load(sid)
        if not s or s["visitor_hash"] != vhash(visitor):
            raise ApiError("not_found")
        if s["ended"] and not ended_ok:  # no checks, questions or events (paid calls, log lines) after /end
            raise ApiError("session_ended")
        if s["mode"] == "live":
            visitors.touch(sid)
        s["visitor"] = visitor  # the cookie, in memory only (tutorials are looked up by it)
        return s

    def events(self, sid: str) -> list:
        return _jsonl(self._path(sid))

    def _write(self, sid: str, line: dict) -> None:
        with self._path(sid).open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        if PROVIDER == "recording" and line["mode"] == "live":
            recorder.timeline(sid, line)

    def log(self, sid: str, event: dict) -> None:
        if event.get("type") == "heartbeat":
            return  # not written; get() already refreshed last_seen
        s = self._load(sid)
        if event["type"] != "session_end" and self._path(sid).stat().st_size > LOG_MAX_BYTES:
            return  # a full log takes no more (quietly): a script can't fill the disk through one session
        self._write(sid, {"session_id": sid, "at": event.get("at") or now_iso(), "type": event["type"],
                          "tutorial_id": s["tutorial_id"], "step_id": event.get("step_id"), "mode": s["mode"],
                          "data": event.get("data") or {}})

    def end(self, sid: str, reason: str, steps_total: int | None = None) -> dict | None:
        events = self.events(sid)
        if not any(e["type"] == "session_end" for e in events):
            self.log(sid, {"type": "session_end", "step_id": None, "at": now_iso(), "data": {"reason": reason}})
            events = self.events(sid)
        self._load(sid)["ended"] = True
        if self._load(sid)["mode"] == "live":
            visitors.touch(sid, end=True)
        return summarise(events, steps_total) if steps_total is not None else None


def _ts(at: str) -> float:
    return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()


def _hardest(events: list, spent: dict):
    """Mistakes first, then questions, then time: time alone is distorted when a replay skips ahead."""
    score = {}
    for e in events:
        if e["type"] in ("mistake_shown", "ask_shown") and e.get("step_id") is not None:
            m, q, t = score.get(e["step_id"], (0, 0, 0.0))
            score[e["step_id"]] = (m + (e["type"] == "mistake_shown"), q + (e["type"] == "ask_shown"), t)
    for sid, t in spent.items():
        m, q, _ = score.get(sid, (0, 0, 0.0))
        score[sid] = (m, q, t)
    return max(score, key=score.get) if score else None


def summarise(events: list, steps_total: int) -> dict:
    """SessionSummary per the data dictionary (FR-100)."""
    adv = [e for e in events if e["type"] == "advance"]
    end = next((e for e in events if e["type"] == "session_end"), None)
    completed = bool(end and end["data"].get("reason") == "completed")
    steps_done = len({e["step_id"] for e in adv})
    if completed and steps_done < steps_total:  # the last step has no advance event
        steps_done += 1
    first_play, spent = {}, {}
    for e in events:
        if e["type"] == "step_play" and e["step_id"] not in first_play:
            first_play[e["step_id"]] = _ts(e["at"])
        if e["type"] == "advance" and e["step_id"] in first_play:
            spent[e["step_id"]] = _ts(e["at"]) - first_play[e["step_id"]]
    start = next((e for e in events if e["type"] == "session_start"), None)
    return {
        "steps_done": min(steps_done, steps_total), "steps_total": steps_total,
        "duration_s": round(_ts(end["at"]) - _ts(start["at"]), 1) if start and end else 0.0,
        "mistakes": [{"step_id": e["step_id"], "failed_checks": e["data"].get("failed_checks", [])}
                     for e in events if e["type"] == "mistake_shown"],
        "hardest_step_id": _hardest(events, spent),
        "rewinds": sum(1 for e in events if e["type"] == "action" and e["data"].get("name") == "again"),
    }


class ClientErrors:
    """Browser errors: one JSON log line each, the last 50 kept in memory for /api/metrics."""

    def __init__(self):
        from collections import deque
        self.items = deque(maxlen=50)

    def add(self, message: str, where: str) -> None:
        self.items.append({"at": now_iso(), "message": message, "where": where})
        speech.log.warning(json.dumps({"event": "client_error", "message": message, "where": where}))

    def recent(self) -> list:
        return list(self.items)[-10:]


# The seams: swap one line to make an area real.
config = FixtureConfig()
tutorials = Tutorials()
recordings = FixtureRecordings()
checks = LiveChecks(FixtureChecks())
voice = LiveVoice(FixtureVoice())
insights = Insights(RUNTIME / "sessions", APP / "eval" / "reports")
sessions = FileSessions(RUNTIME / "sessions")
visitors = Visitors(RUNTIME / "visitors.json", LIMITS, HOSTED)
client_errors = ClientErrors()
