"""HTTP routes per the API spec §2. Data comes from `providers`; this file only validates and shapes."""

import asyncio
import json
import math
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response

from . import providers as p
from . import i18n, safety, voice
from .config import ALLOWED_ORIGIN, MODELS
from .gateway import CapReached, estimate, gateway
from .providers import ApiError

router = APIRouter()


def same_origin(origin: str, scheme: str | None, host: str | None) -> bool:
    """FA_ALLOWED_ORIGIN when set (exact match); else the request's Host (scheme too, except for WebSockets)."""
    if ALLOWED_ORIGIN:
        return origin.rstrip("/") == ALLOWED_ORIGIN
    return origin == f"{scheme}://{host}" if scheme else urlsplit(origin).netloc == host


def visitor(request: Request) -> str:
    return request.state.visitor


def forced(request: Request) -> str | None:
    """Dev-only `?force=` (local runs only), for check fallbacks that are 200 responses, not errors."""
    return None if p.HOSTED else request.query_params.get("force")


@router.get("/healthz")
def healthz():
    return {"ok": True, "live_enabled": p.config.live_enabled}


@router.get("/api/config")
def get_config():
    from .config import LINK_IMPORT
    return {**p.config.get(), "link_import": LINK_IMPORT}


# ---- tutorials ----

@router.get("/api/tutorials")
def list_tutorials(request: Request):
    return p.tutorials.list(visitor(request))


def upload_room(request: Request) -> None:
    """One upload processing per visitor; a full queue (FA_MAX_UPLOAD_JOBS) refuses new ones."""
    if p.tutorials.real() and p.tutorials.busy(visitor(request)):
        raise ApiError("limit_reached", "Your last upload is still being processed. Wait for it, or cancel it.")
    if p.tutorials.real() and p.tutorials.full():
        raise ApiError("limit_reached", "Lots of people are uploading right now. Try again in a few minutes.")


@router.post("/api/tutorials", status_code=202)
async def upload_tutorial(request: Request):
    if not p.config.live_enabled:
        raise ApiError("no_api_key")
    # Cheap refusals first, before the body is read or ffmpeg runs (the size is also enforced while reading: main.py).
    upload_room(request)
    try:  # processing counts against the day and total caps (FR-121)
        gateway.budget.check(None, estimate(MODELS["plan"], {"max_output_tokens": MODELS["plan_max_output_tokens"]}))
    except CapReached as e:
        raise p.capped(e)
    form = await request.form()  # Starlette's cap (1000 files) is far above a manual's 20, so our limit answers
    if form.get("kind") == "manual":  # CR-006: a PDF or page photos become an animated tutorial video
        return await _upload_manual(request, form)
    file, link = form.get("file"), form.get("url")
    linked = None
    if isinstance(link, str) and link.strip() and file is None:  # paste a link: YouTube / Vimeo only
        file, linked = await _download_link(link.strip())
    if file is None:
        raise ApiError("bad_request", "Send the video as the `file` field, or a `url`.")
    if not hasattr(file, "read"):  # a URL or other text in the file field: nothing is fetched (FR-05)
        raise ApiError("invalid_upload", "Upload the video file itself, or paste its link in the link box.")
    # Type and length by ffmpeg, before any model call (NFR-52, FR-04); a URL or text file is invalid_upload (FR-05).
    try:
        tid, src, info, sha256 = await p.tutorials.receive(file, p.UPLOAD_MAX_MB * 1024 * 1024)
    finally:
        if linked:
            file.close()
            shutil.rmtree(linked["path"].parent, ignore_errors=True)
    same = p.tutorials.find_same(sha256, visitor(request))
    if same:  # already a tutorial: open that one; nothing processed, charged or counted against the visitor
        p.tutorials.discard(tid)
        status = p.tutorials.get(same, visitor(request))["status"]
        return JSONResponse({"id": same, "status": status, "duplicate": True}, status_code=200)
    try:
        # again: another upload may have finished arriving while this one did
        upload_room(request)
        p.visitors.admit_upload(visitor(request))
    except ApiError:
        p.tutorials.discard(tid)
        raise
    return p.tutorials.start(tid, src, info, file.filename, visitor(request), sha256,
                             *((linked["creator"], linked["title"]) if linked else ()))


async def _upload_manual(request: Request, form):
    files = [f for f in form.getlist("files") + form.getlist("file") if hasattr(f, "read")]
    if not files:
        raise ApiError("invalid_upload", "Upload the manual as one PDF, or as photos of its pages.")
    tid, src, info, sha256 = await p.tutorials.receive_manual(files, p.UPLOAD_MAX_MB * 1024 * 1024)
    same = p.tutorials.find_same(sha256, visitor(request))
    if same:
        p.tutorials.discard(tid)
        return JSONResponse({"id": same, "status": p.tutorials.get(same, visitor(request))["status"], "duplicate": True},
                            status_code=200)
    try:
        upload_room(request)
        p.visitors.admit_upload(visitor(request))
        return p.tutorials.start(tid, src, info, files[0].filename if len(files) == 1 else "", visitor(request), sha256)
    except BaseException:  # nothing left on disk that no record points to
        p.tutorials.discard(tid)
        raise


class _LocalFile:
    """A downloaded file read like an upload (async read), so links go through the same checks."""

    def __init__(self, path: Path):
        self.f, self.filename = open(path, "rb"), path.name

    async def read(self, n: int) -> bytes:
        return self.f.read(n)

    def close(self) -> None:
        self.f.close()


async def _download_link(url: str):
    from .config import LINK_IMPORT
    from .tutorials import Rejected, fetch_link, link_ok
    if not LINK_IMPORT:
        raise ApiError("invalid_upload", "Links are off on this demo. Upload the video file instead.")
    if not link_ok(url):
        raise ApiError("invalid_upload", "Paste a YouTube or Vimeo link (https), or upload the file.")
    dest = Path(tempfile.mkdtemp(prefix="fa-link-"))
    try:
        got = await asyncio.to_thread(fetch_link, url, p.UPLOAD_MAX_S, dest)
    except Rejected as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise ApiError(e.code, e.message)
    return _LocalFile(got["path"]), got


@router.get("/api/tutorials/{tid}")
def get_tutorial(tid: str, request: Request):
    meta = p.tutorials.get(tid, visitor(request))
    if meta.get("status") != "ready":
        return meta
    return {**meta, "safety": safety.lesson(p.tutorials.details(tid, visitor(request)))}


@router.delete("/api/tutorials/{tid}", status_code=204)
async def delete_tutorial(tid: str, request: Request):
    p.tutorials.delete(tid, visitor(request))
    return Response(status_code=204)


@router.get("/api/tutorials/{tid}/plan")
def get_plan(tid: str, request: Request):
    return p.tutorials.plan(tid, visitor(request))


@router.put("/api/tutorials/{tid}/plan")
async def put_plan(tid: str, request: Request):
    try:
        plan = await request.json()
    except ValueError:
        raise ApiError("invalid_plan")
    if not isinstance(plan, dict):
        raise ApiError("invalid_plan")
    return p.tutorials.save_plan(tid, plan, visitor(request))


def media(path, media_type: str) -> FileResponse:
    """A file with Range support (206) and ETag; `no-cache` revalidates, so regenerated media is never stale."""
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})


@router.get("/api/tutorials/{tid}/details")
async def get_details(tid: str, request: Request, lang: str = "en"):
    steps = p.tutorials.details(tid, visitor(request))
    if lang == "en" or lang not in i18n.LANGS or not p.config.live_enabled:
        return steps
    return i18n.unflatten(steps, await i18n.translate(i18n.flatten(steps), lang))


@router.get("/api/phrases")
async def get_phrases(lang: str = "en"):
    """The coach's fixed spoken lines (config/phrases.json), in the learner's language."""
    if lang == "en" or lang not in i18n.LANGS or not p.config.live_enabled:
        return i18n.PHRASES
    return await i18n.translate(i18n.PHRASES, lang)


@router.get("/api/tutorials/{tid}/video")
def get_video(tid: str, request: Request):
    return media(p.tutorials.video(tid, visitor(request)), "video/mp4")


@router.get("/api/tutorials/{tid}/frames/{step}")
def get_tutorial_frame(tid: str, step: int, request: Request):
    return media(p.tutorials.frame(tid, step, visitor(request)), "image/jpeg")


# ---- sessions ----

@router.post("/api/sessions", status_code=201)
async def start_session(request: Request):
    body = await _json_body(request)
    mode, tid = body.get("mode"), body.get("tutorial_id")
    if mode not in ("live", "replay") or not isinstance(tid, str):
        raise ApiError("bad_request", "Send tutorial_id and mode (live or replay).")
    plan = p.tutorials.plan(tid, visitor(request))
    rid = None
    if mode == "replay":
        rid = body.get("recording_id")
        if not isinstance(rid, str) or p.recordings.meta(rid)["tutorial_id"] != tid:
            raise ApiError("not_found", "Unknown recording for this tutorial.")
    else:
        if body.get("age_confirmed") is not True:
            raise ApiError("consent_required")
        if not p.config.live_enabled:
            raise ApiError("no_api_key")
    pinned = rid or (p.CACHED_RECORDING if p.checks.source()["source"] == "fixture" else None)
    if pinned:  # the cached provider refuses a recording made on another plan or prompt (UT-77)
        p.recordings.pin(pinned, plan, prompt=not rid)  # a replay plays recorded answers: plan only
    lang = body.get("lang") if body.get("lang") in i18n.LANGS else "en"
    ip = request.client.host if request.client else None  # behind a proxy: uvicorn --proxy-headers (Dockerfile)
    sid = p.sessions.start(visitor(request), tid, mode, rid, plan["plan_hash"], lang, ip)
    return {"session_id": sid}


def _require(ok) -> None:
    """Request validation that survives `python -O` (an assert would vanish): the caller turns it into a 400."""
    if not ok:
        raise ValueError("invalid request")


@router.post("/api/sessions/{sid}/checks")
async def run_check(sid: str, request: Request):
    session = p.sessions.get(sid, visitor(request))
    form = await request.form()
    try:
        meta = json.loads(form.get("meta") or "")
        _require(type(meta["step_id"]) is int and type(meta["check_no"]) is int)  # not True
        _require(meta["kind"] in ("full", "recheck", "visibility"))
        _require(meta.get("trigger") in ("still", "check_me", "fixed", "recheck"))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ApiError("bad_request", "meta must be JSON with step_id, kind, check_no, trigger and trigger_at.")
    frame = form.get("frame")
    if frame is None or not hasattr(frame, "read"):
        raise ApiError("bad_request", "Send a JPEG frame as the `frame` field.")
    frame = await frame.read()
    if frame[:3] != b"\xff\xd8\xff":
        raise ApiError("bad_request", "The frame must be a JPEG.")
    if session["busy"]:
        raise ApiError("check_in_progress")
    session["busy"] = True
    try:
        return await p.checks.run(session, meta, forced(request), frame)
    finally:
        session["busy"] = False


def live_only(session: dict) -> None:
    """Replays skip admission (no daily limit), so they never reach the model: no questions, no voice."""
    if session["mode"] != "live":
        raise ApiError("replay_only")


@router.post("/api/sessions/{sid}/questions")
async def ask_question(sid: str, request: Request):
    session = p.sessions.get(sid, visitor(request))
    live_only(session)
    form = await request.form()
    try:
        meta = json.loads(form.get("meta") or "")
        _require(isinstance(meta.get("question"), str) and 0 < len(meta["question"].strip()) <= 500)
        _require(type(meta.get("step_id")) is int)
        hist = meta.get("history", [])  # the browser's recent turns for the coach; never stored
        _require(isinstance(hist, list) and len(hist) <= 20 and all(isinstance(x, dict) for x in hist))
        _require(isinstance(meta.get("last_check", ""), str))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ApiError("bad_request", "meta must be JSON with step_id, question (1–500 characters) and question_no.")
    frame = form.get("frame")
    frame = await frame.read() if frame is not None and hasattr(frame, "read") else b""
    if frame[:3] != b"\xff\xd8\xff":
        raise ApiError("bad_request", "Send the live frame as a JPEG `frame` field.")
    # Only the length is logged (API spec §2); neither the text nor the answer is stored.
    p.sessions.log(sid, {"type": "question", "step_id": meta["step_id"],
                         "data": {"length": len(meta["question"]), "question_no": meta.get("question_no")}})
    return await p.voice.answer(session, meta, frame)


@router.post("/api/sessions/{sid}/watch")
async def watch(sid: str, request: Request):
    """The live coach's look (every 2–3 s while the learner works): status, what it sees, what to say. Not stored."""
    session = p.sessions.get(sid, visitor(request))
    form = await request.form()
    try:
        meta = json.loads(form.get("meta") or "")
        _require(type(meta.get("step_id")) is int)
        el = meta.get("elapsed_s")
        _require(el is None or (type(el) in (int, float) and math.isfinite(el)))
        _require(all(isinstance(meta.get(k, []), list) and len(meta.get(k, [])) <= 10 for k in ("recent", "said")))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ApiError("bad_request", "meta must be JSON with step_id and short recent/said lists.")
    frame = form.get("frame")
    frame = await frame.read() if frame is not None and hasattr(frame, "read") else b""
    if frame[:3] != b"\xff\xd8\xff":
        raise ApiError("bad_request", "Send the live frame as a JPEG `frame` field.")
    return await p.voice.watch(session, meta, frame)


@router.post("/api/sessions/{sid}/transcribe")
async def transcribe(sid: str, request: Request):
    """A push-to-talk clip: audio/wav, mono, 16-bit, 24 kHz, at most 10 s (FR-91). Not stored."""
    session = p.sessions.get(sid, visitor(request))
    live_only(session)
    if int(request.headers.get("content-length") or 0) > voice.CLIP_MAX_S * voice.BYTES_PER_S + 4096:
        raise ApiError("too_long", "Hold to talk records up to 10 seconds.")
    body = await request.body()
    try:
        seconds = voice.clip_seconds(body)
    except ValueError as e:
        raise ApiError("bad_request", str(e))
    if seconds > voice.CLIP_MAX_S + 0.05:
        raise ApiError("too_long", "Hold to talk records up to 10 seconds.")
    return await p.voice.transcribe(session, body, seconds)


@router.websocket("/ws/voice")
async def voice_socket(ws: WebSocket):
    """API spec §3. The HTTP middleware doesn't see WebSockets, so the origin and cookie rules are applied here."""
    origin = ws.headers.get("origin")
    if not origin or not same_origin(origin, None, ws.headers.get("host")):
        return await ws.close(1008)  # before accept: the upgrade is refused with 403 (cross_origin)
    try:
        session = p.sessions.get(ws.query_params.get("session_id") or "", ws.cookies.get("fa_visitor") or "")
    except ApiError:
        return await ws.close(1008)
    if p.voice.mode() != "realtime" or session["mode"] != "live":  # replays never reach the model (live_only)
        return await ws.close(1008)
    await ws.accept()
    await voice.proxy(ws, session, on_refused=p.voice.refused, languages=p.languages(session))


@router.post("/api/sessions/{sid}/events", status_code=204)
async def log_event(sid: str, request: Request):
    p.sessions.get(sid, visitor(request))
    event = await _json_body(request)
    data, step = event.get("data") or {}, event.get("step_id")
    if not isinstance(event.get("type"), str) or not 0 < len(event["type"]) <= 40:
        raise ApiError("bad_request", "An event needs a type.")
    if not isinstance(data, dict) or len(json.dumps(data)) > p.EVENT_DATA_MAX \
            or not (step is None or type(step) is int):
        raise ApiError("bad_request", "An event's data must be a small object and its step_id a number.")
    # The server's clock, not the browser's: the creator view and summaries read these times.
    p.sessions.log(sid, {"type": event["type"], "step_id": step, "data": data})
    return Response(status_code=204)


@router.post("/api/sessions/{sid}/end")
async def end_session(sid: str, request: Request):
    session = p.sessions.get(sid, visitor(request), ended_ok=True)  # the beacon after /end still gets the summary
    body = await _json_body(request)
    reason = body.get("reason") if body.get("reason") in ("completed", "left") else "left"
    steps_total = len(p.tutorials.plan(session["tutorial_id"], visitor(request))["steps"])
    return p.sessions.end(sid, reason, steps_total)


# ---- privacy, monitoring ----

@router.delete("/api/me")
def delete_my_data(request: Request):
    """Delete my data: this browser's uploads, plan edits, session logs and daily counts. The cookie stays (it's what
    finds them) and is cleared by the browser when it expires; nothing else links anything to a person."""
    me = visitor(request)
    uploads, logs = p.tutorials.forget(me), p.sessions.forget(me)
    p.visitors.forget(me)
    return {"uploads": uploads, "session_logs": logs}


@router.post("/api/client-errors", status_code=204)
async def client_error(request: Request):
    """A JavaScript error in a learner's browser: one log line (message, where), never page content. Counted in
    /api/metrics."""
    body = await _json_body(request)
    p.client_errors.add(str(body.get("message", ""))[:300], str(body.get("where", ""))[:200])
    return Response(status_code=204)


@router.get("/api/metrics")
def metrics(request: Request):
    """Operations at a glance (#/ops): model latency and errors (last hour), the API rate limit, spend, live sessions,
    browser errors and the product funnel (last 7 days). Aggregates only. Hosted: only with FA_METRICS_TOKEN (as
    `?token=` or an X-Metrics-Token header), else 404."""
    if p.HOSTED:
        want, got = os.environ.get("FA_METRICS_TOKEN", ""), request.query_params.get("token") or request.headers.get("x-metrics-token") or ""
        if not want or not secrets.compare_digest(want.encode(), got.encode()):
            raise ApiError("not_found")
    from .gateway import gateway as g
    b = g.budget.snapshot()
    return {"models": g.metrics.summary(), "rate_limit": {"rpm": g.limiter.rpm, "load": round(g.limiter.load(), 2),
                                                          "shed_looks": g.limiter.shed},
            "spend": {"day_usd": round(b["day_usd"], 2), "total_usd": round(b["total_usd"], 2), "caps": b["caps"]},
            "client_errors": p.client_errors.recent(), "funnel": p.insights.funnel()}


# ---- recordings, creator, evaluation ----

@router.get("/api/recordings")
def list_recordings():
    return p.recordings.list()


@router.get("/api/recordings/{rid}/timeline")
def get_timeline(rid: str):
    return Response(p.recordings.timeline(rid), media_type="application/x-ndjson")


@router.get("/api/recordings/{rid}/frames/{t}")
def get_recording_frame(rid: str, t: str):
    return media(p.recordings.frame(rid, t), "image/jpeg")


@router.get("/api/creator/{tid}")
def get_creator(tid: str, request: Request):
    return p.insights.creator(tid, p.tutorials.plan(tid, visitor(request)))


@router.get("/api/evaluation/latest")
def get_evaluation():
    return p.insights.evaluation()


async def _json_body(request: Request) -> dict:
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise ApiError("bad_request", "The body must be JSON.")
    if not isinstance(body, dict):
        raise ApiError("bad_request", "The body must be a JSON object.")
    return body


def error_response(err: ApiError) -> JSONResponse:
    return JSONResponse(err.body(), status_code=err.status)
